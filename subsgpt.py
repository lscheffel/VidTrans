"""
LG DLNA Toolkit — PyQt6 + FFmpeg (Versão corrigida)

Esta versão aplica as correções solicitadas:
- Decide modo (extract/reencode) no momento de iniciar a fila.
- Força transcodificação quando em modo "Reencode" (uso explícito de -c:v <codec> e -map 0:v:0 -map 0:a:0?).
- Verbose ligado por checkbox: imprime o stdout/stderr do ffmpeg no terminal em tempo real.
- Gera logfile por job em OUTPUT/logs/job_<basename>_<timestamp>.log com todo o output do ffmpeg/ffprobe.
- Processamento serial (fila) e skip/ignore para arquivos incompatíveis.
- Cancelamento/espera apropriada no closeEvent para evitar "QThread: Destroyed while thread is still running".
- Popula informações via ffprobe ao adicionar arquivos e mostra codecs/legendas/tamanho.

Instruções rápidas:
1) Instale ffmpeg e ffprobe e coloque-os no PATH.
2) Abra o script e rode com Python (recomendo Python 3.10+).
3) Selecione pastas, adicione arquivos, selecione modo, marque "Verbose" se quiser ver o log no terminal, clique em "Iniciar fila".

"""

from PyQt6 import QtWidgets, QtCore
import sys, os, subprocess, shutil, json, traceback, datetime, re

# -------------------------
# Utilitários
# -------------------------
TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+\.?\d*)")

def human_size(n: int) -> str:
    for unit in ("B","KB","MB","GB","TB"):
        if n < 1024.0:
            return f"{n:3.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"

def parse_ffmpeg_time(line: str):
    m = TIME_RE.search(line)
    if not m:
        return None
    h, m_, s = m.groups()
    seconds = int(h) * 3600 + int(m_) * 60 + float(s)
    return seconds

# -------------------------
# Worker: processo de um job
# -------------------------
class FFmpegWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)        # row, percent, message
    finished = QtCore.pyqtSignal(int, bool, str)      # row, success, logfile_or_message

    def __init__(self, job: dict, row: int, settings: dict):
        super().__init__()
        self.job = job
        self.row = row
        self.settings = settings
        self._proc = None
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass

    def _run_cmd_with_logging(self, cmd: list, logfile_handle, duration: float = 0.0, step_msg: str = "") -> bool:
        logfile_handle.write(f"\n=== CMD: {' '.join(cmd)} ===\n")
        logfile_handle.flush()

        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except FileNotFoundError:
            logfile_handle.write("ffmpeg/ffprobe não encontrado no PATH.\n")
            logfile_handle.flush()
            return False

        last_pct = 0
        for line in self._proc.stdout:
            if self._cancel_requested:
                logfile_handle.write("CANCEL REQUESTED - terminando processo...\n")
                logfile_handle.flush()
                try:
                    self._proc.terminate()
                except Exception:
                    pass
                return False

            logfile_handle.write(line)
            logfile_handle.flush()

            if self.settings.get('verbose'):
                print(line, end='')

            t = parse_ffmpeg_time(line)
            if duration and t is not None:
                pct = min(99, int((t / duration) * 100))
                if pct != last_pct:
                    last_pct = pct
                    self.progress.emit(self.row, pct, step_msg)

        rc = self._proc.wait()
        self._proc = None
        logfile_handle.write(f"Processo finalizado com rc={rc}\n")
        logfile_handle.flush()
        return rc == 0

    def run(self):
        try:
            infile = self.job['path']
            outdir = self.settings['outdir']
            mode = self.settings['mode']
            vcodec = self.settings.get('vcodec')
            acodec = self.settings.get('acodec')
            crf = self.settings.get('crf')
            abr = self.settings.get('abr')
            lang_filters = self.settings.get('langs', [])
            verbose = self.settings.get('verbose', False)

            basename = os.path.splitext(os.path.basename(infile))[0]
            ts = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
            logs_dir = os.path.join(outdir, 'logs')
            os.makedirs(logs_dir, exist_ok=True)
            logfile = os.path.join(logs_dir, f"job_{basename}_{ts}.log")

            duration = float(self.job.get('duration') or 0.0)

            with open(logfile, 'w', encoding='utf-8') as log:
                log.write(f"Job {self.row} - input: {infile}\n")
                log.write(f"Settings: mode={mode}, vcodec={vcodec}, acodec={acodec}, crf={crf}, abr={abr}, langs={lang_filters}, verbose={verbose}\n")
                log.write('\n--- ffprobe streams (record) ---\n')
                for s in self.job.get('streams', []):
                    log.write(json.dumps(s, ensure_ascii=False) + '\n')

                try:
                    total, used, free = shutil.disk_usage(outdir)
                    log.write(f"Destino: {outdir} - free={human_size(free)}\n")
                    if free < os.path.getsize(infile):
                        msg = 'Espaço insuficiente na pasta de destino.'
                        log.write(msg + '\n')
                        self.finished.emit(self.row, False, logfile)
                        return
                except Exception as e:
                    log.write(f"Erro acesso disco: {e}\n")

                if mode == 'extract':
                    mp4_friendly_v = any(s.get('codec_type')=='video' and s.get('codec_name') in ('h264','mpeg4','mpeg2video') for s in self.job.get('streams', []))
                    mp4_friendly_a = any(s.get('codec_type')=='audio' and s.get('codec_name') in ('aac','mp3','ac3') for s in self.job.get('streams', []))
                    target_container = 'mp4' if (mp4_friendly_v and mp4_friendly_a) else 'mkv'
                    outfile = os.path.join(outdir, f"{basename}.{target_container}")

                    cmd = ['ffmpeg','-hide_banner','-loglevel','info','-y','-i', infile, '-map','0:v:0','-map','0:a?','-c','copy', outfile]
                    log.write(f"Remux (extract) -> {outfile}\n")
                    ok = self._run_cmd_with_logging(cmd, log, duration, 'Remux')
                    if not ok:
                        self.finished.emit(self.row, False, logfile)
                        return

                    sub_streams = [s for s in self.job.get('streams', []) if s.get('codec_type')=='subtitle']
                    sidx = 0
                    for s in sub_streams:
                        tags = s.get('tags') or {}
                        lang = (tags.get('language') or tags.get('LANGUAGE') or '').lower()
                        matched = False
                        for lf in lang_filters:
                            if lf.lower().split('-')[0] in lang:
                                matched = True
                                break
                        if matched:
                            out_srt = os.path.join(outdir, f"{basename}.{lang or ('sub'+str(sidx+1))}.srt")
                            cmd_s = ['ffmpeg','-hide_banner','-loglevel','info','-y','-i', infile, '-map', f"0:{s.get('index')}", '-c:s','srt', out_srt]
                            log.write(f"Extraindo legenda -> {out_srt}\n")
                            ok = self._run_cmd_with_logging(cmd_s, log, duration, 'ExtrairLegenda')
                            if not ok:
                                log.write(f"Falha ao extrair legenda index={s.get('index')}\n")
                            sidx += 1

                    self.progress.emit(self.row, 100, 'Concluído (extract)')
                    self.finished.emit(self.row, True, logfile)
                    return

                else:
                    out_ext = self.settings.get('out_container') or 'mp4'
                    outfile = os.path.join(outdir, f"{basename}.{out_ext}")

                    v_map = ['-map','0:v:0']
                    a_map = ['-map','0:a:0'] if any(s.get('codec_type')=='audio' for s in self.job.get('streams', [])) else []

                    cmd = ['ffmpeg','-hide_banner','-loglevel','info','-y','-i', infile]
                    cmd += v_map
                    cmd += a_map
                    cmd += ['-c:v', vcodec, '-preset', 'medium', '-crf', str(crf)]
                    if a_map:
                        cmd += ['-c:a', acodec, '-b:a', f"{abr}k"]
                    if out_ext == 'mp4':
                        cmd += ['-movflags','faststart']
                    cmd += [outfile]

                    log.write(f"Reencode -> {outfile}\n")
                    ok = self._run_cmd_with_logging(cmd, log, duration, 'Transcodificando')
                    if not ok:
                        self.finished.emit(self.row, False, logfile)
                        return

                    self.progress.emit(self.row, 100, 'Concluído (reencode)')
                    self.finished.emit(self.row, True, logfile)
                    return

        except Exception as e:
            tb = traceback.format_exc()
            logfile = locals().get('logfile','')
            try:
                with open(logfile, 'a', encoding='utf-8') as log:
                    log.write('\nException:\n')
                    log.write(tb)
            except Exception:
                pass
            self.finished.emit(self.row, False, str(e))

# -------------------------
# GUI principal
# -------------------------
class MainWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('LG DLNA Toolkit — PyQt6 + FFmpeg')
        self.resize(1200, 680)

        self.jobs = []
        self.current_worker: FFmpegWorker | None = None
        self.current_row: int | None = None

        self._build_ui()

    def _build_ui(self):
        layout = QtWidgets.QVBoxLayout(self)

        io_layout = QtWidgets.QHBoxLayout()
        self.in_edit = QtWidgets.QLineEdit()
        self.out_edit = QtWidgets.QLineEdit()
        btn_in = QtWidgets.QPushButton('Entrada')
        btn_out = QtWidgets.QPushButton('Saída')
        btn_in.clicked.connect(lambda: self._pick_dir(self.in_edit))
        btn_out.clicked.connect(lambda: self._pick_dir(self.out_edit))
        io_layout.addWidget(QtWidgets.QLabel('Pasta entrada:'))
        io_layout.addWidget(self.in_edit)
        io_layout.addWidget(btn_in)
        io_layout.addSpacing(12)
        io_layout.addWidget(QtWidgets.QLabel('Pasta saída:'))
        io_layout.addWidget(self.out_edit)
        io_layout.addWidget(btn_out)
        layout.addLayout(io_layout)

        options_layout = QtWidgets.QHBoxLayout()

        mode_box = QtWidgets.QGroupBox('Modo')
        mode_v = QtWidgets.QHBoxLayout(mode_box)
        self.rb_extract = QtWidgets.QRadioButton('Apenas Extração/Remux')
        self.rb_reencode = QtWidgets.QRadioButton('Reencode (transcode)')
        self.rb_extract.setChecked(True)
        mode_v.addWidget(self.rb_extract)
        mode_v.addWidget(self.rb_reencode)
        options_layout.addWidget(mode_box)

        re_box = QtWidgets.QGroupBox('Reencode')
        re_layout = QtWidgets.QFormLayout(re_box)
        self.container_combo = QtWidgets.QComboBox(); self.container_combo.addItems(['mp4','mkv'])
        self.vcodec_combo = QtWidgets.QComboBox(); self.vcodec_combo.addItems(['libx264','libx265','mpeg2video','mpeg1video'])
        self.acodec_combo = QtWidgets.QComboBox(); self.acodec_combo.addItems(['aac','ac3','mp3','libvorbis','libopus'])
        self.crf_spin = QtWidgets.QSpinBox(); self.crf_spin.setRange(10, 40); self.crf_spin.setValue(20)
        self.abr_spin = QtWidgets.QSpinBox(); self.abr_spin.setRange(64,512); self.abr_spin.setValue(192)
        re_layout.addRow('Contêiner:', self.container_combo)
        re_layout.addRow('Codec vídeo:', self.vcodec_combo)
        re_layout.addRow('Codec áudio:', self.acodec_combo)
        re_layout.addRow('CRF:', self.crf_spin)
        re_layout.addRow('Áudio (kbps):', self.abr_spin)
        options_layout.addWidget(re_box)

        misc_box = QtWidgets.QGroupBox('Outros')
        misc_layout = QtWidgets.QVBoxLayout(misc_box)
        self.verbose_chk = QtWidgets.QCheckBox('Verbose (mostrar log do ffmpeg no terminal)')
        self.verbose_chk.setChecked(True)
        misc_layout.addWidget(self.verbose_chk)

        misc_layout.addWidget(QtWidgets.QLabel('Legendas (multi-select) — pré: pt-BR, en'))
        self.lang_list = QtWidgets.QListWidget(); self.lang_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.MultiSelection)
        for key in ['pt-BR','pt','en','es','fr','de','it','ja','zh']:
            it = QtWidgets.QListWidgetItem(key); self.lang_list.addItem(it)
            if key in ('pt-BR','en'): it.setSelected(True)
        misc_layout.addWidget(self.lang_list)

        options_layout.addWidget(misc_box)
        layout.addLayout(options_layout)

        self.table = QtWidgets.QTableWidget(0,9)
        self.table.setHorizontalHeaderLabels(['Arquivo','Status','VCodec/ACodec','Subs','Tamanho','Pular?','Progresso','Log','Ações'])
        self.table.horizontalHeader().setSectionResizeMode(0, QtWidgets.QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table)

        btn_layout = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton('Adicionar da pasta de entrada')
        self.btn_remove = QtWidgets.QPushButton('Remover selecionados')
        self.btn_start = QtWidgets.QPushButton('Iniciar fila')
        self.btn_clear = QtWidgets.QPushButton('Limpar concluídos')
        btn_layout.addWidget(self.btn_add); btn_layout.addWidget(self.btn_remove); btn_layout.addStretch(); btn_layout.addWidget(self.btn_start); btn_layout.addWidget(self.btn_clear)
        layout.addLayout(btn_layout)

        self.btn_add.clicked.connect(self.add_from_input)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_start.clicked.connect(self.start_queue)
        self.btn_clear.clicked.connect(self.clear_done)

    def _pick_dir(self, widget: QtWidgets.QLineEdit):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, 'Escolher pasta')
        if d:
            widget.setText(d)

    def add_from_input(self):
        in_dir = self.in_edit.text().strip()
        out_dir = self.out_edit.text().strip() or in_dir
        if not in_dir or not os.path.isdir(in_dir):
            QtWidgets.QMessageBox.warning(self, 'Pasta inválida', 'Selecione uma pasta de entrada válida.')
            return
        files = [os.path.join(in_dir,f) for f in os.listdir(in_dir) if f.lower().endswith(('.mkv','.mp4','.avi','.mov','.ts','.m2ts'))]
        if not files:
            QtWidgets.QMessageBox.information(self, 'Nada encontrado', 'Nenhum vídeo suportado na pasta.')
            return

        for full in files:
            try:
                out = subprocess.check_output(['ffprobe','-v','quiet','-print_format','json','-show_streams', full], text=True)
                info = json.loads(out)
                streams = info.get('streams', [])
            except Exception as e:
                streams = []

            vcodec = next((s.get('codec_name') for s in streams if s.get('codec_type')=='video'), '?')
            acodec = next((s.get('codec_name') for s in streams if s.get('codec_type')=='audio'), '?')
            subs = ','.join((s.get('tags') or {}).get('language','') for s in streams if s.get('codec_type')=='subtitle')
            filesize = os.path.getsize(full)
            duration = 0.0
            try:
                dur_out = subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1', full], text=True)
                duration = float(dur_out.strip() or 0)
            except Exception:
                duration = 0.0

            compatible = 'Sim'
            message = ''
            if vcodec == '?':
                compatible = 'Não'
                message = 'Sem faixa de vídeo'
            else:
                try:
                    total, used, free = shutil.disk_usage(out_dir)
                    if free < filesize:
                        compatible = 'Não'
                        message = 'Espaço insuficiente no destino'
                except Exception:
                    pass

            job = {
                'path': full,
                'streams': streams,
                'filesize': filesize,
                'duration': duration,
                'message': message,
                'compatible': (compatible == 'Sim')
            }
            self.jobs.append(job)
            self._append_job_row(job)

    def _append_job_row(self, job: dict):
        row = self.table.rowCount()
        self.table.insertRow(row)
        fname = os.path.basename(job['path'])
        self.table.setItem(row,0, QtWidgets.QTableWidgetItem(fname))
        self.table.setItem(row,1, QtWidgets.QTableWidgetItem('Pendente' if job.get('compatible') else 'Incompatível'))
        vcodecs = ','.join({s.get('codec_name') for s in job.get('streams', []) if s.get('codec_type')=='video'})
        acodecs = ','.join({s.get('codec_name') for s in job.get('streams', []) if s.get('codec_type')=='audio'})
        self.table.setItem(row,2, QtWidgets.QTableWidgetItem(f"{vcodecs}/{acodecs}"))
        subs = ','.join((s.get('tags') or {}).get('language','') for s in job.get('streams', []) if s.get('codec_type')=='subtitle')
        self.table.setItem(row,3, QtWidgets.QTableWidgetItem(subs))
        self.table.setItem(row,4, QtWidgets.QTableWidgetItem(human_size(job.get('filesize',0))))

        chk = QtWidgets.QCheckBox()
        chk.setChecked(False)
        chk.stateChanged.connect(lambda state, r=row: self._set_skip(r, state))
        self.table.setCellWidget(row,5, chk)

        pbar = QtWidgets.QProgressBar()
        pbar.setValue(0)
        self.table.setCellWidget(row,6, pbar)

        self.table.setItem(row,7, QtWidgets.QTableWidgetItem(''))

        btn_rm = QtWidgets.QPushButton('Remover')
        btn_rm.clicked.connect(lambda _, r=row: self._remove_row(r))
        w = QtWidgets.QWidget(); lay = QtWidgets.QHBoxLayout(w); lay.setContentsMargins(0,0,0,0); lay.addWidget(btn_rm); lay.addStretch()
        self.table.setCellWidget(row,8, w)

    def _set_skip(self, row: int, state: int):
        try:
            self.jobs[row]['skip'] = (state == QtCore.Qt.CheckState.Checked)
        except Exception:
            pass

    def _remove_row(self, row: int):
        if self.current_row == row and self.current_worker and self.current_worker.isRunning():
            QtWidgets.QMessageBox.warning(self, 'Remover', 'Não é possível remover um item em execução.')
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
            st = self.table.item(r,1)
            if st and st.text() in ('Concluído','Falhou','Ignorado'):
                rows.append(r)
        for r in reversed(rows):
            self.table.removeRow(r)
            try: self.jobs.pop(r)
            except: pass

    def start_queue(self):
        if not self.jobs:
            return
        out_dir = self.out_edit.text().strip() or self.in_edit.text().strip()
        if not out_dir:
            QtWidgets.QMessageBox.warning(self, 'Erro', 'Selecione uma pasta de saída.')
            return

        settings = {
            'mode': 'extract' if self.rb_extract.isChecked() else 'reencode',
            'vcodec': self.vcodec_combo.currentText(),
            'acodec': self.acodec_combo.currentText(),
            'crf': self.crf_spin.value(),
            'abr': self.abr_spin.value(),
            'outdir': out_dir,
            'langs': [it.text() for it in self.lang_list.selectedItems()],
            'verbose': self.verbose_chk.isChecked(),
            'out_container': self.container_combo.currentText()
        }

        self.settings = settings
        self.btn_add.setEnabled(False); self.btn_remove.setEnabled(False); self.btn_start.setEnabled(False)
        self._next_index = 0
        self._process_next()

    def _process_next(self):
        idx = None
        while self._next_index < len(self.jobs):
            j = self.jobs[self._next_index]
            if not j.get('compatible', True) and j.get('skip'):
                self.table.setItem(self._next_index,1, QtWidgets.QTableWidgetItem('Ignorado'))
                self._next_index += 1
                continue
            if j.get('compatible', True) or (not j.get('compatible', True) and j.get('skip')):
                idx = self._next_index
                break
            QtWidgets.QMessageBox.warning(self, 'Incompatível', f"Arquivo '{os.path.basename(j['path'])}' marcado como incompatível: {j.get('message','')}.\nMarque 'Pular' ou remova antes de continuar.")
            self.btn_add.setEnabled(True); self.btn_remove.setEnabled(True); self.btn_start.setEnabled(True)
            return

        if idx is None:
            self.btn_add.setEnabled(True); self.btn_remove.setEnabled(True); self.btn_start.setEnabled(True)
            return

        job = self.jobs[idx]
        row = idx
        self.table.setItem(row,1, QtWidgets.QTableWidgetItem('Executando'))
        self.current_row = row

        worker = FFmpegWorker(job, row, self.settings)
        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        self.current_worker = worker
        worker.start()

    def _on_progress(self, row: int, pct: int, msg: str):
        try:
            pbar = self.table.cellWidget(row,6)
            if isinstance(pbar, QtWidgets.QProgressBar):
                pbar.setValue(pct)
            self.table.setItem(row,1, QtWidgets.QTableWidgetItem(msg if msg else f'{pct}%'))
        except Exception:
            pass

    def _on_finished(self, row: int, success: bool, logfile_or_msg: str):
        try:
            self.table.setItem(row,1, QtWidgets.QTableWidgetItem('Concluído' if success else 'Falhou'))
            self.table.setItem(row,7, QtWidgets.QTableWidgetItem(logfile_or_msg))
            if success:
                pbar = self.table.cellWidget(row,6)
                if isinstance(pbar, QtWidgets.QProgressBar): pbar.setValue(100)
        except Exception:
            pass

        self.current_worker = None
        self.current_row = None
        self._next_index += 1
        QtCore.QTimer.singleShot(200, self._process_next)

    def closeEvent(self, event):
        if self.current_worker and self.current_worker.isRunning():
            ok = QtWidgets.QMessageBox.question(self, 'Fechar', 'Há um job em execução. Cancelar e sair?')
            if ok != QtWidgets.QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            try:
                self.current_worker.cancel()
                self.current_worker.wait(5000)
            except Exception:
                pass
        event.accept()

def main():
    app = QtWidgets.QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
