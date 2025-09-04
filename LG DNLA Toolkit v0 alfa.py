import os
import sys
import shutil
import subprocess
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple

from PyQt6 import QtCore, QtGui, QtWidgets

# -----------------------------
# Helpers
# -----------------------------

def which_ffmpeg() -> Optional[str]:
    """Return ffmpeg path if available in PATH or alongside the script."""
    candidates = [
        shutil.which("ffmpeg"),
        os.path.join(os.path.dirname(sys.argv[0]), "ffmpeg.exe"),
        os.path.join(os.path.dirname(sys.argv[0]), "ffmpeg"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


def which_ffprobe() -> Optional[str]:
    candidates = [
        shutil.which("ffprobe"),
        os.path.join(os.path.dirname(sys.argv[0]), "ffprobe.exe"),
        os.path.join(os.path.dirname(sys.argv[0]), "ffprobe"),
    ]
    for c in candidates:
        if c and os.path.exists(c):
            return c
    return None


VIDEO_EXTS = {".mkv", ".mp4", ".avi", ".mov", ".ts", ".m2ts", ".wmv", ".flv"}

LANG_MAP = {
    "pt": ["por", "pt"],
    "pt-BR": ["por", "pb", "pt-BR"],
    "en": ["eng", "en"],
    "es": ["spa", "es"],
    "fr": ["fra", "fre", "fr"],
    "de": ["deu", "ger", "de"],
    "it": ["ita", "it"],
    "ja": ["jpn", "ja"],
    "zh": ["zho", "chi", "zh"],
}

# -----------------------------
# Data models
# -----------------------------

@dataclass
class Job:
    input_path: str
    output_dir: str
    mode: str  # "extract" or "reencode"
    extract_variant: str = "vid_aud_leg_unified"  # one of: sep_tracks | vid_aud__leg | vid_aud_leg_unified
    target_container: str = "mp4"  # for reencode or unified remux
    target_codec_v: str = "h264"    # reencode video codec
    target_codec_a: str = "aac"     # reencode audio codec
    crf: int = 20
    abr_kbps: int = 192
    lang_filters: List[str] = field(default_factory=lambda: ["pt", "pt-BR", "en"])  # for subtitles extraction

    # Runtime state
    status: str = "queued"  # queued, running, done, error, canceled
    progress: int = 0
    log: str = ""


# -----------------------------
# Worker thread
# -----------------------------

class FFmpegWorker(QtCore.QThread):
    progress_signal = QtCore.pyqtSignal(int, str)  # percent, message
    status_signal = QtCore.pyqtSignal(str)         # new status
    finished_signal = QtCore.pyqtSignal(bool, str) # success, message

    def __init__(self, job: Job, ffmpeg_path: str, ffprobe_path: str):
        super().__init__()
        self.job = job
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self._proc: Optional[subprocess.Popen] = None
        self._cancel = False

    def cancel(self):
        self._cancel = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ---- Core helpers ----
    def _ffprobe_duration(self, path: str) -> float:
        try:
            out = subprocess.check_output([
                self.ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=nw=1:nk=1",
                path
            ], stderr=subprocess.STDOUT, text=True)
            return float(out.strip())
        except Exception:
            return 0.0

    _time_re = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")

    def _parse_progress(self, line: str) -> Optional[float]:
        m = self._time_re.search(line)
        if not m:
            return None
        h, m_, s = m.groups()
        seconds = int(h) * 3600 + int(m_) * 60 + float(s)
        return seconds

    def _run_ffmpeg(self, args: List[str], duration: float, step_msg: str) -> bool:
        if self._cancel:
            return False
        self.progress_signal.emit(self.job.progress, step_msg)
        self._proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
        for line in self._proc.stdout:
            if self._cancel:
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                return False
            # Update progress
            t = self._parse_progress(line)
            if duration > 0 and t is not None:
                pct = min(99, int((t / duration) * 100))
                self.progress_signal.emit(pct, step_msg)
        rc = self._proc.wait()
        return rc == 0

    # ---- Build commands per mode ----
    def _streams_info(self, path: str) -> Dict:
        try:
            out = subprocess.check_output([
                self.ffprobe, "-v", "error",
                "-print_format", "json",
                "-show_streams",
                path
            ], text=True)
            return json.loads(out)
        except Exception:
            return {"streams": []}

    def _match_lang(self, stream: Dict) -> bool:
        # Accept if no filter set or language matches configured filters
        tags = stream.get("tags", {}) or {}
        lang = (tags.get("language") or tags.get("LANGUAGE") or "").lower()
        for k in self.job.lang_filters:
            for cand in LANG_MAP.get(k, [k]):
                if cand.lower() == lang:
                    return True
        # also accept if filter contains wildcard "*"
        return ("*" in self.job.lang_filters)

    def run(self):
        self.status_signal.emit("running")
        self.job.status = "running"

        try:
            os.makedirs(self.job.output_dir, exist_ok=True)
            src = self.job.input_path
            base = os.path.splitext(os.path.basename(src))[0]
            duration = self._ffprobe_duration(src)

            if self.job.mode == "extract":
                ok, msg = self._do_extract(src, base, duration)
            else:
                ok, msg = self._do_reencode(src, base, duration)

            if self._cancel:
                self.status_signal.emit("canceled")
                self.finished_signal.emit(False, "Cancelado")
                return

            if ok:
                self.status_signal.emit("done")
                self.finished_signal.emit(True, msg or "Concluído")
            else:
                self.status_signal.emit("error")
                self.finished_signal.emit(False, msg or "Falhou")
        except Exception as e:
            self.status_signal.emit("error")
            self.finished_signal.emit(False, f"Erro: {e}")

    # ---- Extract modes ----
    def _do_extract(self, src: str, base: str, duration: float) -> Tuple[bool, str]:
        info = self._streams_info(src)
        streams = info.get("streams", [])
        video_idxs = [s["index"] for s in streams if s.get("codec_type") == "video"]
        audio_idxs = [s["index"] for s in streams if s.get("codec_type") == "audio"]
        sub_idxs = [s["index"] for s in streams if s.get("codec_type") == "subtitle" and self._match_lang(s)]

        # Determine container for unified remux (MKV is safest for copy of many codecs)
        unified_container = "mkv"

        if self.job.extract_variant == "sep_tracks":
            # 1) video-only MP4
            dst_v = os.path.join(self.job.output_dir, f"{base}.video.mp4")
            args_v = [self.ffmpeg, "-y", "-i", src, "-map", f"0:v:0", "-c", "copy", dst_v]
            if not self._run_ffmpeg(args_v, duration, "Extraindo vídeo…"):
                return False, "Falha ao extrair vídeo"

            # 2) each audio stream to individual file preserving codec
            for i, a_idx in enumerate(audio_idxs):
                dst_a = os.path.join(self.job.output_dir, f"{base}.audio{ i+1 }.mka")
                args_a = [self.ffmpeg, "-y", "-i", src, "-map", f"0:{a_idx}", "-c", "copy", dst_a]
                if not self._run_ffmpeg(args_a, duration, f"Extraindo áudio {i+1}…"):
                    return False, f"Falha ao extrair áudio {i+1}"

            # 3) selected subtitles to .srt
            if sub_idxs:
                for k, s_idx in enumerate(sub_idxs):
                    dst_s = os.path.join(self.job.output_dir, f"{base}.sub{ k+1 }.srt")
                    args_s = [self.ffmpeg, "-y", "-i", src, "-map", f"0:{s_idx}", dst_s]
                    if not self._run_ffmpeg(args_s, duration, f"Extraindo legenda {k+1}…"):
                        return False, f"Falha ao extrair legenda {k+1}"
            return True, "Extração concluída (faixas separadas)"

        elif self.job.extract_variant == "vid_aud__leg":
            # Remux video+all audio, plus separate .srt files for chosen subs
            dst = os.path.join(self.job.output_dir, f"{base}.{unified_container}")
            args = [self.ffmpeg, "-y", "-i", src, "-c", "copy"]
            # map video
            if video_idxs:
                args += ["-map", f"0:{video_idxs[0]}"]
            # map all audio
            for a_idx in audio_idxs:
                args += ["-map", f"0:{a_idx}"]
            args += [dst]
            if not self._run_ffmpeg(args, duration, "Remux: vídeo+áudio…"):
                return False, "Falha no remux vídeo+áudio"

            # Extract subs
            for k, s_idx in enumerate(sub_idxs):
                dst_s = os.path.join(self.job.output_dir, f"{base}.sub{ k+1 }.srt")
                args_s = [self.ffmpeg, "-y", "-i", src, "-map", f"0:{s_idx}", dst_s]
                if not self._run_ffmpeg(args_s, duration, f"Extraindo legenda {k+1}…"):
                    return False, f"Falha ao extrair legenda {k+1}"
            return True, "Remux (vídeo+áudio) e legendas separadas concluídos"

        else:  # vid_aud_leg_unified
            dst = os.path.join(self.job.output_dir, f"{base}.{unified_container}")
            args = [self.ffmpeg, "-y", "-i", src, "-c", "copy"]
            # map video
            if video_idxs:
                args += ["-map", f"0:{video_idxs[0]}"]
            # map all audio
            for a_idx in audio_idxs:
                args += ["-map", f"0:{a_idx}"]
            # map selected subs
            for s_idx in sub_idxs:
                args += ["-map", f"0:{s_idx}"]
            args += [dst]
            if not self._run_ffmpeg(args, duration, "Remux unificado (V/A/Leg)…"):
                return False, "Falha no remux unificado"
            return True, "Remux unificado concluído"

    # ---- Reencode ----
    def _do_reencode(self, src: str, base: str, duration: float) -> Tuple[bool, str]:
        # Default: MP4 H.264 + AAC with CRF
        ext = self.job.target_container.lower()
        dst = os.path.join(self.job.output_dir, f"{base}.{ext}")
        vcodec = self.job.target_codec_v
        acodec = self.job.target_codec_a

        args = [
            self.ffmpeg, "-y", "-i", src,
            "-c:v", vcodec,
            "-crf", str(self.job.crf),
            "-preset", "veryfast",
            "-c:a", acodec,
        ]
        # If AAC, set bitrate
        if acodec.lower() == "aac":
            args += ["-b:a", f"{self.job.abr_kbps}k"]

        # Safeguard for mp4: drop non-AAC audio without transcode by coercing transcode
        # (we already set c:a, so okay). For subtitles, mp4 doesn't support .srt inside; skip subs.
        # Map only first video and all audios
        args += ["-map", "0:v:0"]
        args += ["-map", "0:a?" ]

        args += [dst]

        ok = self._run_ffmpeg(args, duration, "Transcodificando…")
        if not ok:
            return False, "Falha na transcodificação"
        return True, "Transcodificação concluída"


# -----------------------------
# GUI
# -----------------------------

class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LG DLNA Toolkit – MKV→MP4 / Legendas (PyQt6 + FFmpeg)")
        self.resize(1050, 720)
        self.ffmpeg = which_ffmpeg()
        self.ffprobe = which_ffprobe()
        if not self.ffmpeg or not self.ffprobe:
            QtWidgets.QMessageBox.warning(self, "FFmpeg não encontrado",
                "Instale o FFmpeg e garanta que 'ffmpeg' e 'ffprobe' estejam no PATH.")

        self._jobs: List[Job] = []
        self._workers: Dict[int, FFmpegWorker] = {}

        self._build_ui()

    # ---- UI Construction ----
    def _build_ui(self):
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        # Top: input/output dirs
        io_box = QtWidgets.QGroupBox("Pastas")
        io_layout = QtWidgets.QGridLayout(io_box)

        self.in_edit = QtWidgets.QLineEdit()
        self.out_edit = QtWidgets.QLineEdit()
        btn_in = QtWidgets.QToolButton(text="…")
        btn_out = QtWidgets.QToolButton(text="…")
        btn_in.clicked.connect(self.choose_in_dir)
        btn_out.clicked.connect(self.choose_out_dir)
        io_layout.addWidget(QtWidgets.QLabel("Entrada:"), 0, 0)
        io_layout.addWidget(self.in_edit, 0, 1)
        io_layout.addWidget(btn_in, 0, 2)
        io_layout.addWidget(QtWidgets.QLabel("Saída:"), 1, 0)
        io_layout.addWidget(self.out_edit, 1, 1)
        io_layout.addWidget(btn_out, 1, 2)

        layout.addWidget(io_box)

        # Mode: extract or reencode
        mode_box = QtWidgets.QGroupBox("Modo")
        mode_layout = QtWidgets.QHBoxLayout(mode_box)
        self.rb_extract = QtWidgets.QRadioButton("Apenas extração / remux")
        self.rb_reencode = QtWidgets.QRadioButton("Reencode")
        self.rb_extract.setChecked(True)
        mode_layout.addWidget(self.rb_extract)
        mode_layout.addWidget(self.rb_reencode)
        layout.addWidget(mode_box)

        # Extraction controls
        extract_box = QtWidgets.QGroupBox("Controles de extração")
        extract_layout = QtWidgets.QGridLayout(extract_box)
        self.rb_sep_tracks = QtWidgets.QRadioButton("vid, audio, legenda (faixas separadas)")
        self.rb_vid_aud_leg = QtWidgets.QRadioButton("vid/aud, leg (V+A unificados; legendas separadas)")
        self.rb_unified_all = QtWidgets.QRadioButton("vid/aud/leg (unificados)")
        self.rb_unified_all.setChecked(True)
        extract_layout.addWidget(self.rb_sep_tracks, 0, 0, 1, 2)
        extract_layout.addWidget(self.rb_vid_aud_leg, 1, 0, 1, 2)
        extract_layout.addWidget(self.rb_unified_all, 2, 0, 1, 2)

        extract_layout.addWidget(QtWidgets.QLabel("Legendas (múltipla seleção por idioma):"), 3, 0)
        self.lang_list = QtWidgets.QListWidget()
        self.lang_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        for key in ["pt-BR", "pt", "en", "es", "fr", "de", "it", "ja", "zh"]:
            item = QtWidgets.QListWidgetItem(key)
            self.lang_list.addItem(item)
            if key in ("pt-BR", "pt", "en"):
                item.setSelected(True)
        extract_layout.addWidget(self.lang_list, 4, 0, 1, 2)
        layout.addWidget(extract_box)

        # Reencode controls
        reenc_box = QtWidgets.QGroupBox("Controles de reencode")
        reenc_layout = QtWidgets.QGridLayout(reenc_box)
        self.combo_container = QtWidgets.QComboBox()
        self.combo_container.addItems(["mp4", "mkv"])  # containers alvo
        self.combo_vcodec = QtWidgets.QComboBox()
        self.combo_vcodec.addItems(["h264", "hevc"])  # codecs de vídeo
        self.combo_acodec = QtWidgets.QComboBox()
        self.combo_acodec.addItems(["aac", "ac3"])     # codecs de áudio
        self.spin_crf = QtWidgets.QSpinBox()
        self.spin_crf.setRange(10, 35)
        self.spin_crf.setValue(20)
        self.spin_abr = QtWidgets.QSpinBox()
        self.spin_abr.setRange(64, 512)
        self.spin_abr.setValue(192)
        reenc_layout.addWidget(QtWidgets.QLabel("Contêiner:"), 0, 0)
        reenc_layout.addWidget(self.combo_container, 0, 1)
        reenc_layout.addWidget(QtWidgets.QLabel("Vídeo:"), 1, 0)
        reenc_layout.addWidget(self.combo_vcodec, 1, 1)
        reenc_layout.addWidget(QtWidgets.QLabel("Áudio:"), 2, 0)
        reenc_layout.addWidget(self.combo_acodec, 2, 1)
        reenc_layout.addWidget(QtWidgets.QLabel("CRF:"), 3, 0)
        reenc_layout.addWidget(self.spin_crf, 3, 1)
        reenc_layout.addWidget(QtWidgets.QLabel("Áudio (kbps):"), 4, 0)
        reenc_layout.addWidget(self.spin_abr, 4, 1)
        layout.addWidget(reenc_box)

        # Queue table
        queue_box = QtWidgets.QGroupBox("Fila")
        queue_layout = QtWidgets.QVBoxLayout(queue_box)
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Arquivo", "Status", "Progresso", "Mensagem", "Ações"])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        queue_layout.addWidget(self.table)

        btns = QtWidgets.QHBoxLayout()
        self.btn_scan = QtWidgets.QPushButton("Adicionar da pasta de entrada")
        self.btn_start = QtWidgets.QPushButton("Iniciar fila")
        self.btn_clear = QtWidgets.QPushButton("Limpar concluídos")
        btns.addWidget(self.btn_scan)
        btns.addStretch(1)
        btns.addWidget(self.btn_start)
        btns.addWidget(self.btn_clear)
        queue_layout.addLayout(btns)
        layout.addWidget(queue_box)

        # Connections
        self.btn_scan.clicked.connect(self.scan_input_dir)
        self.btn_start.clicked.connect(self.start_queue)
        self.btn_clear.clicked.connect(self.clear_done)

        # Visual polish
        self._apply_style()

    def _apply_style(self):
        self.setStyleSheet(
            """
            QGroupBox { font-weight: bold; margin-top: 10px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 3px 0 3px; }
            QTableWidget { gridline-color: #ddd; }
            QPushButton { padding: 6px 12px; }
            """
        )

    # ---- Actions ----
    def choose_in_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Escolher pasta de entrada")
        if d:
            self.in_edit.setText(d)

    def choose_out_dir(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Escolher pasta de saída")
        if d:
            self.out_edit.setText(d)

    def _gather_langs(self) -> List[str]:
        return [i.text() for i in self.lang_list.selectedItems()]

    def _current_extract_variant(self) -> str:
        if self.rb_sep_tracks.isChecked():
            return "sep_tracks"
        if self.rb_vid_aud_leg.isChecked():
            return "vid_aud__leg"
        return "vid_aud_leg_unified"

    def _build_job_for_file(self, path: str) -> Job:
        out = self.out_edit.text().strip() or os.path.dirname(path)
        mode = "extract" if self.rb_extract.isChecked() else "reencode"
        job = Job(
            input_path=path,
            output_dir=out,
            mode=mode,
            extract_variant=self._current_extract_variant(),
            target_container=self.combo_container.currentText(),
            target_codec_v=self.combo_vcodec.currentText(),
            target_codec_a=self.combo_acodec.currentText(),
            crf=self.spin_crf.value(),
            abr_kbps=self.spin_abr.value(),
            lang_filters=self._gather_langs(),
        )
        return job

    def scan_input_dir(self):
        in_dir = self.in_edit.text().strip()
        if not in_dir or not os.path.isdir(in_dir):
            QtWidgets.QMessageBox.warning(self, "Pasta inválida", "Selecione uma pasta de entrada válida.")
            return
        files = [os.path.join(in_dir, f) for f in os.listdir(in_dir)
                 if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
        if not files:
            QtWidgets.QMessageBox.information(self, "Nada encontrado", "Nenhum vídeo suportado na pasta.")
            return
        for f in files:
            self._append_job(self._build_job_for_file(f))

    def _append_job(self, job: Job):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(os.path.basename(job.input_path)))
        self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(job.status))

        # progress bar
        pbar = QtWidgets.QProgressBar()
        pbar.setValue(0)
        self.table.setCellWidget(row, 2, pbar)

        self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(""))

        # actions: cancel button
        btn_cancel = QtWidgets.QPushButton("Cancelar")
        btn_cancel.clicked.connect(lambda _, r=row: self.cancel_job(r))
        w = QtWidgets.QWidget()
        hl = QtWidgets.QHBoxLayout(w)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(btn_cancel)
        hl.addStretch(1)
        self.table.setCellWidget(row, 4, w)

        self._jobs.append(job)

    def cancel_job(self, row: int):
        worker = self._workers.get(row)
        if worker:
            worker.cancel()

    def start_queue(self):
        if not self.ffmpeg or not self.ffprobe:
            QtWidgets.QMessageBox.warning(self, "FFmpeg/FFprobe ausentes",
                                          "Instale FFmpeg/FFprobe e coloque no PATH.")
            return
        for row, job in enumerate(self._jobs):
            if job.status in ("done", "running"):
                continue
            self._run_row(row, job)

    def _run_row(self, row: int, job: Job):
        worker = FFmpegWorker(job, self.ffmpeg, self.ffprobe)
        self._workers[row] = worker

        def on_progress(pct: int, msg: str):
            pbar = self.table.cellWidget(row, 2)
            if isinstance(pbar, QtWidgets.QProgressBar):
                pbar.setValue(pct)
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(msg))

        def on_status(st: str):
            self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(st))

        def on_finished(success: bool, message: str):
            self.table.setItem(row, 3, QtWidgets.QTableWidgetItem(message))
            pbar = self.table.cellWidget(row, 2)
            if isinstance(pbar, QtWidgets.QProgressBar):
                pbar.setValue(100 if success else pbar.value())

        worker.progress_signal.connect(on_progress)
        worker.status_signal.connect(on_status)
        worker.finished_signal.connect(on_finished)
        worker.start()

    def clear_done(self):
        # remove rows with status done or error
        to_remove = []
        for r in range(self.table.rowCount()):
            st_item = self.table.item(r, 1)
            if st_item and st_item.text() in ("done", "error", "canceled"):
                to_remove.append(r)
        for idx in reversed(to_remove):
            self.table.removeRow(idx)
            try:
                self._jobs.pop(idx)
                self._workers.pop(idx, None)
            except Exception:
                pass


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
