
#!/usr/bin/env python3
"""
LG DLNA Toolkit — PyQt6 + FFmpeg batch remux/extract/reencode
Features requested:
- Default: Apenas Extração/Remux (Vid/Aud unificados em mp4 quando possível + legendas .srt separadas)
- Pre-seleção de legendas: pt-BR e en
- Reencode: mais codecs suportados (video: h264/hevc/mpeg2/mpeg1; audio: aac/ac3/mp3/vorbis/opus)
- Fila serial (não paralela)
- Popula codecs e legendas via ffprobe ao adicionar à fila
- Permite remoção manual de itens da fila
- Confirma espaço disponível na pasta de destino
- Marca arquivos incompatíveis e permite pular aqueles com flag
Notes:
- Requires ffmpeg and ffprobe available in PATH.
- Tested logic for mapping streams via ffprobe 'index' values.
"""

import os
import sys
import json
import shutil
import subprocess
import re
from dataclasses import dataclass, field
from typing import List, Optional

from PyQt6 import QtWidgets, QtCore, QtGui

# -------------------------
# Utilities
# -------------------------

def which_bin(name):
    path = shutil.which(name)
    return path

FFMPEG = which_bin("ffmpeg")
FFPROBE = which_bin("ffprobe")

def readable_size(n):
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"

def run_ffprobe_collect_streams(path):
    try:
        out = subprocess.check_output([FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", path], text=True)
        info = json.loads(out)
        streams = info.get("streams", [])
        return streams, None
    except Exception as e:
        return None, str(e)

def probe_duration(path):
    try:
        out = subprocess.check_output([FFPROBE,"-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",path], text=True)
        return float(out.strip())
    except Exception:
        return 0.0

_time_re = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")
def parse_ffmpeg_time(line):
    m = _time_re.search(line)
    if not m:
        return None
    h, mm, ss = m.groups()
    return int(h)*3600 + int(mm)*60 + float(ss)

# -------------------------
# Data models
# -------------------------

@dataclass
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str
    language: Optional[str] = None

@dataclass
class Job:
    input_path: str
    out_dir: str
    mode: str = "extract"  # 'extract' or 'reencode'
    extract_variant: str = "vid_aud__leg"  # sep_tracks | vid_aud__leg | vid_aud_leg_unified
    target_container: str = "mp4"
    target_vcodec: str = "h264"
    target_acodec: str = "aac"
    crf: int = 20
    abr_kbps: int = 192
    lang_filters: List[str] = field(default_factory=lambda: ["pt-BR","en"])

    # populated by ffprobe
    streams: List[StreamInfo] = field(default_factory=list)
    filesize: int = 0
    duration: float = 0.0

    # runtime
    status: str = "queued"  # queued, incompatible, running, done, error, skipped
    message: str = ""
    skip_if_incompatible: bool = False

# -------------------------
# Worker: single job processor
# -------------------------

class JobWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, str)  # percent, message
    finished = QtCore.pyqtSignal(bool, str)  # success, message
    status_changed = QtCore.pyqtSignal(str)

    def __init__(self, job: Job):
        super().__init__()
        self.job = job
        self._proc = None
        self._cancel = False

    def cancel(self):
        self._cancel = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    def _run_process_and_track(self, args, duration, step_msg):
        # spawn ffmpeg, parse output for time= and update percent
        try:
            self.status_changed.emit(step_msg)
            self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except FileNotFoundError:
            self.progress.emit(0, "ffmpeg não encontrado")
            return False

        for line in self._proc.stdout:
            if self._cancel:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                return False
            t = parse_ffmpeg_time(line)
            if t is not None and duration > 0:
                pct = min(99, int((t/duration)*100))
                self.progress.emit(pct, step_msg)
        rc = self._proc.wait()
        return rc == 0

    def _match_sub_lang(self, lang):
        if not lang:
            return False
        lang = lang.lower()
        for lf in self.job.lang_filters:
            for cand in (lf.lower(), lf.split("-")[0].lower()):
                if cand in lang:
                    return True
        return False

    def run(self):
        job = self.job
        job.status = "running"
        self.status_changed.emit("running")
        duration = job.duration or probe_duration(job.input_path)
        job.duration = duration

        # Simple compatibility guard: must have at least one video stream
        video_streams = [s for s in job.streams if s.codec_type == "video"]
        audio_streams = [s for s in job.streams if s.codec_type == "audio"]
        subtitle_streams = [s for s in job.streams if s.codec_type == "subtitle"]

        if not video_streams:
            self.finished.emit(False, "Sem faixa de vídeo")
            return

        # Determine container friendliness for mp4
        mp4_vid_ok = any(s.codec_name in ("h264","mpeg4","mpeg2video","mpeg1video","hevc") for s in job.streams if s.codec_type=="video")
        mp4_aud_ok = any(s.codec_name in ("aac","mp3","ac3") for s in job.streams if s.codec_type=="audio")
        target_ext = job.target_container
        if target_ext == "mp4" and not (mp4_vid_ok and mp4_aud_ok):
            # fallback to mkv to avoid forced transcode
            unified_ext = "mkv"
        else:
            unified_ext = target_ext

        try:
            if job.mode == "extract":
                base = os.path.splitext(os.path.basename(job.input_path))[0]
                # pick first video index
                vid_idx = video_streams[0].index

                if job.extract_variant == "sep_tracks":
                    # write video-only mp4/mkv (remux)
                    out_video = os.path.join(job.out_dir, f"{base}.video.{unified_ext}")
                    args_v = [FFMPEG,"-y","-i",job.input_path,"-map",f"0:{vid_idx}","-c","copy",out_video]
                    ok = self._run_process_and_track(args_v, duration, "Extraindo vídeo")
                    if not ok:
                        self.finished.emit(False, "Falha ao extrair vídeo")
                        return
                    # extract each audio stream
                    for idx, a in enumerate(audio_streams):
                        out_audio = os.path.join(job.out_dir, f"{base}.audio{idx+1}.mka")
                        args_a = [FFMPEG,"-y","-i",job.input_path,"-map",f"0:{a.index}","-c","copy",out_audio]
                        ok = self._run_process_and_track(args_a, duration, f"Extraindo áudio {idx+1}")
                        if not ok:
                            self.finished.emit(False, f"Falha ao extrair áudio {idx+1}")
                            return
                    # extract matched subtitles as srt
                    s_count = 0
                    for s in subtitle_streams:
                        if self._match_sub_lang(s.language):
                            out_s = os.path.join(job.out_dir, f"{base}.{s.language or 'sub'}.{s_count+1}.srt")
                            args_s = [FFMPEG,"-y","-i",job.input_path,"-map",f"0:{s.index}","-c:s","srt",out_s]
                            ok = self._run_process_and_track(args_s, duration, f"Extraindo legenda {s_count+1}")
                            if not ok:
                                self.finished.emit(False, f"Falha ao extrair legenda {s_count+1}")
                                return
                            s_count += 1
                    self.finished.emit(True, "Extração (faixas separadas) concluída")
                    return

                elif job.extract_variant == "vid_aud__leg":
                    # remux video + all audio into a single file (prefer mp4 if friendly)
                    out_unified = os.path.join(job.out_dir, f"{base}.{unified_ext}")
                    args = [FFMPEG,"-y","-i",job.input_path,"-c","copy"]
                    args += ["-map", f"0:{vid_idx}"]
                    for a in audio_streams:
                        args += ["-map", f"0:{a.index}"]
                    args += [out_unified]
                    ok = self._run_process_and_track(args, duration, "Remux: vídeo+áudio")
                    if not ok:
                        self.finished.emit(False, "Falha no remux vídeo+áudio")
                        return
                    # extract matching subtitles to srt
                    s_count = 0
                    for s in subtitle_streams:
                        if self._match_sub_lang(s.language):
                            out_s = os.path.join(job.out_dir, f"{base}.{s.language or 'sub'}.{s_count+1}.srt")
                            args_s = [FFMPEG,"-y","-i",job.input_path,"-map",f"0:{s.index}","-c:s","srt",out_s]
                            ok = self._run_process_and_track(args_s, duration, f"Extraindo legenda {s_count+1}")
                            if not ok:
                                self.finished.emit(False, f"Falha ao extrair legenda {s_count+1}")
                                return
                            s_count += 1
                    self.finished.emit(True, "Remux + legendas concluído")
                    return

                else:  # vid_aud_leg_unified
                    out_unified = os.path.join(job.out_dir, f"{base}.{unified_ext}")
                    args = [FFMPEG,"-y","-i",job.input_path,"-c","copy"]
                    args += ["-map", f"0:{vid_idx}"]
                    for a in audio_streams:
                        args += ["-map", f"0:{a.index}"]
                    # map subs that match
                    for s in subtitle_streams:
                        if self._match_sub_lang(s.language):
                            args += ["-map", f"0:{s.index}"]
                    args += [out_unified]
                    ok = self._run_process_and_track(args, duration, "Remux unificado (V/A/Leg)")
                    if not ok:
                        self.finished.emit(False, "Falha no remux unificado")
                        return
                    self.finished.emit(True, "Remux unificado concluído")
                    return

            else:
                # Reencode path (transcode)
                base = os.path.splitext(os.path.basename(job.input_path))[0]
                out_file = os.path.join(job.out_dir, f"{base}.{job.target_container}")
                vcodec = job.target_vcodec
                acodec = job.target_acodec
                args = [FFMPEG,"-y","-i",job.input_path,"-c:v",vcodec,"-crf",str(job.crf),"-preset","veryfast","-c:a",acodec,"-b:a",f"{job.abr_kbps}k",out_file]
                ok = self._run_process_and_track(args, duration, "Transcodificando")
                if not ok:
                    self.finished.emit(False, "Falha na transcodificação")
                    return
                self.finished.emit(True, "Transcodificação concluída")
                return

        except Exception as e:
            self.finished.emit(False, f"Erro interno: {e}")
            return

# -------------------------
# GUI main window
# -------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LG DLNA Toolkit — MKV→MP4 / Legendas (PyQt6 + FFmpeg)")
        self.resize(1100, 700)

        if not FFMPEG or not FFPROBE:
            QtWidgets.QMessageBox.warning(self, "FFmpeg/FFprobe ausentes",
                "ffmpeg e/ou ffprobe não foram encontrados no PATH. Instale-os antes de usar.")

        self.jobs: List[Job] = []
        self.current_worker: Optional[JobWorker] = None
        self.current_row: Optional[int] = None

        self._build_ui()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        v = QtWidgets.QVBoxLayout(central)

        # IO selection
        form = QtWidgets.QHBoxLayout()
        self.in_edit = QtWidgets.QLineEdit()
        self.out_edit = QtWidgets.QLineEdit()
        btn_in = QtWidgets.QPushButton("Pasta Entrada")
        btn_out = QtWidgets.QPushButton("Pasta Saída")
        btn_in.clicked.connect(lambda: self._pick_dir(self.in_edit))
        btn_out.clicked.connect(lambda: self._pick_dir(self.out_edit))
        form.addWidget(QtWidgets.QLabel("Entrada:"))
        form.addWidget(self.in_edit)
        form.addWidget(btn_in)
        form.addSpacing(10)
        form.addWidget(QtWidgets.QLabel("Saída:"))
        form.addWidget(self.out_edit)
        form.addWidget(btn_out)
        v.addLayout(form)

        # Mode selection
        mode_box = QtWidgets.QGroupBox("Modo (padrão: Apenas Extração/Remux)")
        mode_layout = QtWidgets.QHBoxLayout(mode_box)
        self.rb_extract = QtWidgets.QRadioButton("Apenas Extração/Remux")
        self.rb_reencode = QtWidgets.QRadioButton("Reencode")
        self.rb_extract.setChecked(True)
        mode_layout.addWidget(self.rb_extract)
        mode_layout.addWidget(self.rb_reencode)
        v.addWidget(mode_box)

        # Extraction options
        ext_box = QtWidgets.QGroupBox("Controles de extração (padrões: Vid/Aud + Legendas → mp4 quando possível, .srt separadas)")
        ext_layout = QtWidgets.QGridLayout(ext_box)
        self.rb_sep = QtWidgets.QRadioButton("vid, audio, legenda (faixas separadas)")
        self.rb_va_leg = QtWidgets.QRadioButton("vid/aud, leg (V+A unificados; legendas separadas)")
        self.rb_unified = QtWidgets.QRadioButton("vid/aud/leg (unificados)")
        self.rb_va_leg.setChecked(True)
        ext_layout.addWidget(self.rb_sep, 0, 0)
        ext_layout.addWidget(self.rb_va_leg, 1, 0)
        ext_layout.addWidget(self.rb_unified, 2, 0)
        ext_layout.addWidget(QtWidgets.QLabel("Legendas (multi-select) — pré: pt-BR, en"), 3, 0)
        self.lang_list = QtWidgets.QListWidget()
        self.lang_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        for key in ["pt-BR","pt","en","es","fr","de","it","ja","zh"]:
            it = QtWidgets.QListWidgetItem(key)
            self.lang_list.addItem(it)
            if key in ("pt-BR","en"):
                it.setSelected(True)
        ext_layout.addWidget(self.lang_list, 4, 0, 1, 2)
        v.addWidget(ext_box)

        # Reencode options
        re_box = QtWidgets.QGroupBox("Controles de reencode")
        re_layout = QtWidgets.QGridLayout(re_box)
        self.combo_container = QtWidgets.QComboBox()
        self.combo_container.addItems(["mp4","mkv","avi"])
        self.combo_vcodec = QtWidgets.QComboBox()
        self.combo_vcodec.addItems(["libx264","libx265","mpeg2video","mpeg1video"])
        self.combo_acodec = QtWidgets.QComboBox()
        self.combo_acodec.addItems(["aac","ac3","mp3","libvorbis","libopus"])
        self.spin_crf = QtWidgets.QSpinBox(); self.spin_crf.setRange(10,51); self.spin_crf.setValue(20)
        self.spin_abr = QtWidgets.QSpinBox(); self.spin_abr.setRange(64,512); self.spin_abr.setValue(192)
        re_layout.addWidget(QtWidgets.QLabel("Contêiner:"),0,0); re_layout.addWidget(self.combo_container,0,1)
        re_layout.addWidget(QtWidgets.QLabel("Vídeo:"),1,0); re_layout.addWidget(self.combo_vcodec,1,1)
        re_layout.addWidget(QtWidgets.QLabel("Áudio:"),2,0); re_layout.addWidget(self.combo_acodec,2,1)
        re_layout.addWidget(QtWidgets.QLabel("CRF:"),3,0); re_layout.addWidget(self.spin_crf,3,1)
        re_layout.addWidget(QtWidgets.QLabel("Áudio kbps:"),4,0); re_layout.addWidget(self.spin_abr,4,1)
        v.addWidget(re_box)

        # Queue table
        self.table = QtWidgets.QTableWidget(0,9)
        self.table.setHorizontalHeaderLabels(["Arquivo","Status","VCodec/ACodec","Subs","Tamanho","Pular?","Progresso","Mensagem","Ações"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.table)

        # Buttons
        h = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Adicionar da pasta de entrada")
        self.btn_remove = QtWidgets.QPushButton("Remover selecionados")
        self.btn_start = QtWidgets.QPushButton("Iniciar fila")
        self.btn_clear = QtWidgets.QPushButton("Limpar concluídos")
        h.addWidget(self.btn_add); h.addWidget(self.btn_remove); h.addStretch(); h.addWidget(self.btn_start); h.addWidget(self.btn_clear)
        v.addLayout(h)

        # Connect
        self.btn_add.clicked.connect(self.add_from_input)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_start.clicked.connect(self.start_queue)
        self.btn_clear.clicked.connect(self.clear_done)

    def _pick_dir(self, widget):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Escolher pasta")
        if d:
            widget.setText(d)

    def _gather_lang_filters(self):
        return [i.text() for i in self.lang_list.selectedItems()]

    def _current_extract_variant(self):
        if self.rb_sep.isChecked(): return "sep_tracks"
        if self.rb_unified.isChecked(): return "vid_aud_leg_unified"
        return "vid_aud__leg"

    def add_from_input(self):
        in_dir = self.in_edit.text().strip()
        out_dir = self.out_edit.text().strip() or in_dir
        if not in_dir or not os.path.isdir(in_dir):
            QtWidgets.QMessageBox.warning(self, "Pasta inválida", "Selecione uma pasta de entrada válida.")
            return
        files = [os.path.join(in_dir,f) for f in os.listdir(in_dir) if f.lower().endswith(('.mkv','.mp4','.avi','.mov','.ts','.m2ts'))]
        if not files:
            QtWidgets.QMessageBox.information(self, "Nada", "Nenhum vídeo suportado encontrado.")
            return
        for full in files:
            # create job and probe
            job = Job(input_path=full, out_dir=out_dir)
            job.mode = "extract" if self.rb_extract.isChecked() else "reencode"
            job.extract_variant = self._current_extract_variant()
            job.target_container = self.combo_container.currentText()
            job.target_vcodec = self.combo_vcodec.currentText()
            job.target_acodec = self.combo_acodec.currentText()
            job.crf = self.spin_crf.value()
            job.abr_kbps = self.spin_abr.value()
            job.lang_filters = self._gather_lang_filters()

            streams, err = run_ffprobe_collect_streams(full)
            if err or streams is None:
                job.status = "incompatible"
                job.message = f"ffprobe falhou: {err}"
            else:
                job.filesize = os.path.getsize(full)
                job.duration = probe_duration(full)
                for s in streams:
                    idx = s.get("index")
                    ctype = s.get("codec_type")
                    cname = s.get("codec_name")
                    tags = s.get("tags") or {}
                    lang = tags.get("language") or tags.get("LANGUAGE")
                    job.streams.append(StreamInfo(index=idx, codec_type=ctype, codec_name=cname, language=lang))
                # basic check
                if not any(s.codec_type=="video" for s in job.streams):
                    job.status = "incompatible"
                    job.message = "Sem faixa de vídeo"
                else:
                    job.status = "queued"
            # check space in out_dir
            try:
                total, used, free = shutil.disk_usage(job.out_dir)
                if free < job.filesize:
                    job.status = "incompatible"
                    job.message = "Espaço insuficiente em destino"
            except Exception:
                pass

            self.jobs.append(job)
            self._append_job_row(job)

    def _append_job_row(self, job: Job):
        row = self.table.rowCount()
        self.table.insertRow(row)
        fname = os.path.basename(job.input_path)
        self.table.setItem(row,0,QtWidgets.QTableWidgetItem(fname))
        self.table.setItem(row,1,QtWidgets.QTableWidgetItem(job.status))
        vcodecs = ",".join({s.codec_name for s in job.streams if s.codec_type=="video"})
        acodecs = ",".join({s.codec_name for s in job.streams if s.codec_type=="audio"})
        self.table.setItem(row,2,QtWidgets.QTableWidgetItem(f"{vcodecs}/{acodecs}"))
        subs = ",".join([s.language or "" for s in job.streams if s.codec_type=="subtitle"])
        self.table.setItem(row,3,QtWidgets.QTableWidgetItem(subs))
        self.table.setItem(row,4,QtWidgets.QTableWidgetItem(readable_size(job.filesize)))
        # skip checkbox
        chk = QtWidgets.QCheckBox()
        chk.setChecked(job.skip_if_incompatible)
        chk.stateChanged.connect(lambda st, r=row: self._set_skip_flag(r, st))
        self.table.setCellWidget(row,5,chk)
        # progress bar
        pbar = QtWidgets.QProgressBar()
        pbar.setValue(0)
        self.table.setCellWidget(row,6,pbar)
        self.table.setItem(row,7,QtWidgets.QTableWidgetItem(job.message))
        # actions (remove)
        btn = QtWidgets.QPushButton("Remover")
        btn.clicked.connect(lambda _, r=row: self._remove_row(r))
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.addWidget(btn); lay.addStretch()
        self.table.setCellWidget(row,8,w)

    def _set_skip_flag(self, row, state):
        try:
            self.jobs[row].skip_if_incompatible = bool(state)
        except Exception:
            pass

    def _remove_row(self, row):
        if self.current_row == row and self.current_worker and self.current_worker.isRunning():
            QtWidgets.QMessageBox.warning(self, "Remover", "Não é possível remover item em execução.")
            return
        try:
            self.table.removeRow(row)
            self.jobs.pop(row)
        except Exception:
            pass

    def remove_selected(self):
        sels = self.table.selectionModel().selectedRows()
        rows = sorted([r.row() for r in sels], reverse=True)
        for r in rows:
            self._remove_row(r)

    def clear_done(self):
        rows = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r,1)
            if it and it.text() in ("done","error","skipped"):
                rows.append(r)
        for r in reversed(rows):
            self.table.removeRow(r)
            try: self.jobs.pop(r)
            except: pass

    def start_queue(self):
        if not self.jobs:
            return
        # disable adding/removing while running optionally
        self.btn_add.setEnabled(False)
        self.btn_remove.setEnabled(False)
        self.process_next()

    def process_next(self):
        # if worker running, ignore
        if self.current_worker and self.current_worker.isRunning():
            return
        next_idx = None
        for idx, job in enumerate(self.jobs):
            if job.status == "queued":
                next_idx = idx; break
            if job.status == "incompatible" and job.skip_if_incompatible:
                job.status = "skipped"
                self._update_row(idx)
        if next_idx is None:
            # finished queue
            self.btn_add.setEnabled(True)
            self.btn_remove.setEnabled(True)
            return
        job = self.jobs[next_idx]
        if job.status == "incompatible" and not job.skip_if_incompatible:
            self._update_row(next_idx)
            QtWidgets.QMessageBox.warning(self, "Incompatível", f"Arquivo '{os.path.basename(job.input_path)}' incompatível:\\n{job.message}\\nMarque 'Pular' para ignorar ou remova o item.")
            self.btn_add.setEnabled(True)
            self.btn_remove.setEnabled(True)
            return

        # start worker
        self.current_row = next_idx
        self.current_worker = JobWorker(job)
        # connect signals
        self.current_worker.progress.connect(lambda pct,msg: self._on_progress(self.current_row,pct,msg))
        self.current_worker.status_changed.connect(lambda st: self._on_status(self.current_row,st))
        self.current_worker.finished.connect(lambda ok,msg: self._on_finished(self.current_row,ok,msg))
        # update status
        job.status = "running"
        self._update_row(next_idx)
        self.current_worker.start()

    def _on_progress(self, row, pct, msg):
        try:
            pbar = self.table.cellWidget(row,6)
            if isinstance(pbar, QtWidgets.QProgressBar):
                pbar.setValue(pct)
            self.table.setItem(row,7,QtWidgets.QTableWidgetItem(msg))
        except Exception:
            pass

    def _on_status(self, row, st):
        try:
            self.jobs[row].message = st
            self.table.setItem(row,1,QtWidgets.QTableWidgetItem(self.jobs[row].status))
            self.table.setItem(row,7,QtWidgets.QTableWidgetItem(st))
        except Exception:
            pass

    def _on_finished(self, row, ok, msg):
        try:
            self.jobs[row].status = "done" if ok else "error"
            self.jobs[row].message = msg
            self._update_row(row)
        except Exception:
            pass
        # cleanup and process next
        self.current_worker = None
        self.current_row = None
        QtCore.QTimer.singleShot(200, self.process_next)

    def _update_row(self, row):
        if row < 0 or row >= len(self.jobs): return
        job = self.jobs[row]
        self.table.setItem(row,1,QtWidgets.QTableWidgetItem(job.status))
        vcodecs = ",".join({s.codec_name for s in job.streams if s.codec_type=="video"})
        acodecs = ",".join({s.codec_name for s in job.streams if s.codec_type=="audio"})
        self.table.setItem(row,2,QtWidgets.QTableWidgetItem(f"{vcodecs}/{acodecs}"))
        subs = ",".join([s.language or "" for s in job.streams if s.codec_type=="subtitle"])
        self.table.setItem(row,3,QtWidgets.QTableWidgetItem(subs))
        self.table.setItem(row,4,QtWidgets.QTableWidgetItem(readable_size(job.filesize)))
        chk = self.table.cellWidget(row,5)
        if isinstance(chk, QtWidgets.QCheckBox):
            chk.setChecked(job.skip_if_incompatible)
        pbar = self.table.cellWidget(row,6)
        if isinstance(pbar, QtWidgets.QProgressBar):
            if job.status == "done": pbar.setValue(100)
        self.table.setItem(row,7,QtWidgets.QTableWidgetItem(job.message))

# -------------------------
# Run
# -------------------------

def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
