## extract + convert subtitles from MKV or standalone subtitle files
## uses ffmpeg/ffprobe and mkvextract

## Produzido com o ChatGPT
# https://chatgpt.com/c/68b90138-0454-8322-9639-dd381e10964e


import os
import sys
import json
import shutil
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QAction, QIcon, QPalette, QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFileDialog, QTabWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QComboBox, QCheckBox, QMessageBox, QHeaderView, QProgressBar
)

# =============================
# Environment & Defaults (Windows paths as requested)
# =============================
FFMPEG_PATH = r"C:\\Program Files\\FFMPEG\\bin\\ffmpeg.exe"
FFPROBE_PATH = r"C:\\Program Files\\FFMPEG\\bin\\ffprobe.exe"
MKVEXTRACT_PATH = r"C:\\Program Files\\MKVToolNix\\mkvextract.exe"
MKVINFO_PATH = r"C:\\Program Files\\MKVToolNix\\mkvinfo.exe"
SRTDEF_PATH = Path(r"C:\\subtitles")  # default input directory
TEMP_FOLDER = Path(r"E:\\DB\\TempSubs")

# Ensure temp folder exists
TEMP_FOLDER.mkdir(parents=True, exist_ok=True)

# =============================
# Helpers
# =============================
TEXT_ENCODINGS = {
    "UTF-8 (sem BOM)": "utf-8",
    "UTF-8 +BOM": "utf-8-sig",
    "ANSI Latin I (1252)": "cp1252",
    "UTF7": "utf-7",
}

SUB_FORMATS = ["srt", "ass", "ssa", "mks"]

TEXT_SUB_CODECS = {
    "subrip": "srt",
    "ass": "ass",
    "ssa": "ssa",
    "webvtt": "vtt",
    "text": "txt",
    "mov_text": "srt",  # when extracted via ffmpeg it comes as srt
}

IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle"}


def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    """Run external command and return (returncode, stdout, stderr)."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="ignore")
        return p.returncode, p.stdout, p.stderr
    except Exception as e:
        return 1, "", str(e)


def ffprobe_json(path: Path) -> Optional[dict]:
    cmd = [FFPROBE_PATH, "-v", "error", "-show_entries", "format:streams", "-print_format", "json", str(path)]
    rc, out, err = run_cmd(cmd)
    if rc == 0 and out.strip():
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return None
    return None


def file_size_str(num_bytes: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.2f} PB"


def safe_stem(path: Path) -> str:
    return path.stem


def unique_path(base: Path, overwrite: bool) -> Path:
    if overwrite or not base.exists():
        return base
    i = 1
    while True:
        candidate = base.with_stem(f"{base.stem} ({i})")
        if not candidate.exists():
            return candidate
        i += 1


def detect_text_encoding(path: Path) -> str:
    """Best-effort detection without mandatory deps."""
    # Try common encodings first
    tried = ["utf-8-sig", "utf-8", "cp1252", "iso-8859-1", "utf-16", "utf-7"]
    for enc in tried:
        try:
            with open(path, "r", encoding=enc) as f:
                f.read()
                return enc
        except Exception:
            continue
    # Try chardet if available
    try:
        import chardet  # type: ignore
        with open(path, "rb") as f:
            raw = f.read()
        det = chardet.detect(raw)
        enc = det.get("encoding")
        if enc:
            return enc
    except Exception:
        pass
    return "utf-8"  # fallback


def read_text(path: Path, encoding: Optional[str] = None) -> str:
    enc = encoding or detect_text_encoding(path)
    with open(path, "r", encoding=enc, errors="replace") as f:
        return f.read()


def write_text(path: Path, text: str, encoding_label: str) -> None:
    enc = TEXT_ENCODINGS.get(encoding_label, "utf-8")
    # Ensure parent exists
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding=enc, errors="replace", newline="\n") as f:
        f.write(text)


def convert_subtitle_with_ffmpeg(src: Path, dst: Path, dst_ext: str) -> Tuple[bool, str]:
    """Use ffmpeg to convert subtitle formats. dst_ext without dot."""
    dst = dst.with_suffix(f".{dst_ext}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    # Map for ffmpeg muxing/codec selection
    # For text subtitle conversion, -c:s srt/ass/ssa; for mks, copy stream into Matroska
    if dst_ext in {"srt", "ass", "ssa"}:
        codec = dst_ext
        cmd = [FFMPEG_PATH, "-y", "-i", str(src), "-map", "0:s:0", "-c:s", codec, str(dst)]
    elif dst_ext == "mks":
        # Make a Matroska subtitle file by remuxing first subtitle stream
        cmd = [FFMPEG_PATH, "-y", "-i", str(src), "-map", "0:s:0", "-c", "copy", str(dst)]
    else:
        return False, f"Formato de destino não suportado: {dst_ext}"

    rc, out, err = run_cmd(cmd)
    if rc != 0 or not dst.exists():
        return False, err or out or "Falha ao converter legendas com ffmpeg"
    return True, str(dst)


def reencode_text_file(path: Path, target_label: str) -> None:
    text = read_text(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    write_text(tmp, text, target_label)
    # Replace
    path.unlink(missing_ok=True)
    tmp.replace(path)


def extract_all_subs_from_mkv(mkv_path: Path, out_dir: Path, overwrite: bool) -> List[Path]:
    """Extract all subtitle tracks using ffmpeg (text tracks) or mkvextract (fallback). Returns list of extracted files."""
    meta = ffprobe_json(mkv_path)
    if not meta:
        return []
    streams = meta.get("streams", [])
    subs = [s for s in streams if s.get("codec_type") == "subtitle"]
    extracted: List[Path] = []

    # Prefer ffmpeg for text subs; use mkvextract for copy of any subtype
    for idx, s in enumerate(subs):
        codec = s.get("codec_name") or ""
        lang = s.get("tags", {}).get("language", f"und")
        tidx = s.get("index", idx)

        # Decide output ext for raw extraction
        if codec in TEXT_SUB_CODECS:
            ext = TEXT_SUB_CODECS[codec]
        elif codec in IMAGE_SUB_CODECS:
            ext = "sup"  # bitmap sup for PGS/DVD
        else:
            ext = "sub"  # generic

        base = out_dir / f"{safe_stem(mkv_path)}.track{tidx}.{lang}.{ext}"
        out_file = unique_path(base, overwrite)

        # Try ffmpeg extraction of the specific subtitle stream
        # Use -map 0:s:m:language:lang if language available, else by index
        if lang != "und":
            map_selector = f"0:s:m:language:{lang}?{idx}"
        else:
            map_selector = f"0:s:{idx}"

        cmd = [FFMPEG_PATH, "-y" if overwrite else "-n", "-i", str(mkv_path), "-map", map_selector, "-c:s", "copy", str(out_file)]
        rc, out, err = run_cmd(cmd)
        if rc == 0 and out_file.exists():
            extracted.append(out_file)
            continue

        # Fallback to mkvextract by track ID (we need the track id as in mkvextract, which differs from ffprobe index).
        # Use mkvextract with simple heuristic: use sequential track numbers; if it fails, skip.
        # NOTE: robust mapping requires mkvmerge --identify; keeping simple here.
        track_id = idx  # heuristic
        mkv_out = unique_path(out_dir / f"{safe_stem(mkv_path)}.track{track_id}.{lang}.{ext}", overwrite)
        cmd2 = [MKVEXTRACT_PATH, "tracks", str(mkv_path), f"{track_id}:{mkv_out}"]
        rc2, out2, err2 = run_cmd(cmd2)
        if rc2 == 0 and mkv_out.exists():
            extracted.append(mkv_out)

    return extracted


# =============================
# Workers
# =============================
@dataclass
class MkxItem:
    path: Path
    size: int
    duration: float
    subs_summary: str


class ScanMkvsThread(QThread):
    itemsReady = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder

    def run(self):
        items: List[MkxItem] = []
        try:
            for p in sorted(self.folder.glob("*.mkv")):
                meta = ffprobe_json(p)
                if not meta:
                    continue
                size = p.stat().st_size
                duration = float(meta.get("format", {}).get("duration", 0.0) or 0.0)
                subs = []
                for s in meta.get("streams", []):
                    if s.get("codec_type") == "subtitle":
                        lang = s.get("tags", {}).get("language", "und")
                        codec = s.get("codec_name", "?")
                        subs.append(f"{lang}:{codec}")
                items.append(MkxItem(p, size, duration, ", ".join(subs) or "-") )
        except Exception as e:
            self.error.emit(str(e))
            return
        self.itemsReady.emit(items)


class ProcessMkvsThread(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, in_dir: Path, out_dir: Path, target_fmt: str, target_enc_label: str, overwrite: bool):
        super().__init__()
        self.in_dir = in_dir
        self.out_dir = out_dir
        self.target_fmt = target_fmt
        self.target_enc_label = target_enc_label
        self.overwrite = overwrite

    def run(self):
        mkvs = sorted(self.in_dir.glob("*.mkv"))
        total = len(mkvs)
        if total == 0:
            self.done.emit("Nenhum MKV encontrado.")
            return
        count = 0
        for mkv in mkvs:
            count += 1
            self.progress.emit(int(100 * (count-1) / total), f"Processando {mkv.name}…")
            try:
                # 1) extrair todas as legendas
                sub_out_dir = TEMP_FOLDER / mkv.stem
                sub_out_dir.mkdir(parents=True, exist_ok=True)
                extracted = extract_all_subs_from_mkv(mkv, sub_out_dir, self.overwrite)

                # 2) converter cada uma para formato desejado
                for sub in extracted:
                    # pular bitmap se destino for texto
                    if self.target_fmt in {"srt", "ass", "ssa"} and sub.suffix.lower() == ".sup":
                        # Não há OCR aqui
                        continue

                    # Se o arquivo já estiver no formato desejado, apenas copiar para out_dir antes da re-codificação
                    if sub.suffix.lower() == f".{self.target_fmt}":
                        dst_base = self.out_dir / f"{mkv.stem}.{sub.name.split('.')[-2]}.{self.target_fmt}"
                        dst = unique_path(dst_base, self.overwrite)
                        shutil.copy2(sub, dst)
                        # 3) re-encode text
                        if self.target_fmt in {"srt", "ass", "ssa"}:
                            reencode_text_file(dst, self.target_enc_label)
                        continue

                    # Converter via ffmpeg
                    dst_base = self.out_dir / f"{mkv.stem}.{sub.name.split('.')[-2]}.{self.target_fmt}"
                    dst_final = unique_path(dst_base, self.overwrite)
                    ok, msg = convert_subtitle_with_ffmpeg(sub, dst_final, self.target_fmt)
                    if not ok:
                        # tentar conversão 2-etapas: converter para srt e depois remuxar
                        if self.target_fmt == "mks":
                            tmp_srt = TEMP_FOLDER / (dst_final.stem + ".srt")
                            ok2, msg2 = convert_subtitle_with_ffmpeg(sub, tmp_srt, "srt")
                            if ok2:
                                ok3, msg3 = convert_subtitle_with_ffmpeg(tmp_srt, dst_final, "mks")
                                tmp_srt.unlink(missing_ok=True)
                                if not ok3:
                                    self.error.emit(f"Falha em {mkv.name}: {msg3}")
                            else:
                                self.error.emit(f"Falha em {mkv.name}: {msg2}")
                        else:
                            self.error.emit(f"Falha em {mkv.name}: {msg}")
                        continue

                    # 3) re-encode text if applicable
                    if self.target_fmt in {"srt", "ass", "ssa"}:
                        reencode_text_file(dst_final, self.target_enc_label)

            except Exception as e:
                self.error.emit(f"Erro em {mkv.name}: {e}")
        self.progress.emit(100, "Concluído.")
        self.done.emit("Processamento finalizado.")


class ScanSubsThread(QThread):
    itemsReady = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder

    def run(self):
        try:
            rows = []
            for ext in ("*.srt", "*.ass", "*.ssa", "*.vtt", "*.sub", "*.txt"):
                for p in sorted(self.folder.glob(ext)):
                    enc = detect_text_encoding(p)
                    rows.append((p, p.suffix.lower().lstrip("."), enc))
        except Exception as e:
            self.error.emit(str(e))
            return
        self.itemsReady.emit(rows)


class ProcessSubsThread(QThread):
    progress = pyqtSignal(int, str)
    done = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, in_dir: Path, out_dir: Path, target_fmt: str, target_enc_label: str, overwrite: bool):
        super().__init__()
        self.in_dir = in_dir
        self.out_dir = out_dir
        self.target_fmt = target_fmt
        self.target_enc_label = target_enc_label
        self.overwrite = overwrite

    def run(self):
        subs = []
        for ext in ("*.srt", "*.ass", "*.ssa", "*.vtt", "*.sub", "*.txt"):
            subs.extend(sorted(self.in_dir.glob(ext)))
        total = len(subs)
        if total == 0:
            self.done.emit("Nenhuma legenda encontrada.")
            return
        for i, sub in enumerate(subs, start=1):
            self.progress.emit(int(100 * (i-1)/total), f"Convertendo {sub.name}…")
            try:
                base_name = sub.stem
                dst_base = self.out_dir / f"{base_name}.{self.target_fmt}"
                dst = unique_path(dst_base, self.overwrite)

                # Se já estiver no formato alvo, apenas copiar
                if sub.suffix.lower() == f".{self.target_fmt}":
                    shutil.copy2(sub, dst)
                else:
                    ok, msg = convert_subtitle_with_ffmpeg(sub, dst, self.target_fmt)
                    if not ok:
                        self.error.emit(f"Falha em {sub.name}: {msg}")
                        continue

                # Re-encode se for texto
                if self.target_fmt in {"srt", "ass", "ssa"}:
                    reencode_text_file(dst, self.target_enc_label)

            except Exception as e:
                self.error.emit(f"Erro em {sub.name}: {e}")
        self.progress.emit(100, "Concluído.")
        self.done.emit("Conversão concluída.")


# =============================
# UI Components
# =============================
class FolderPicker(QWidget):
    changed = pyqtSignal(Path)

    def __init__(self, label: str, default: Optional[Path] = None):
        super().__init__()
        lay = QHBoxLayout(self)
        self.label = QLabel(label)
        self.edit = QLineEdit()
        if default:
            self.edit.setText(str(default))
        btn = QPushButton("Procurar…")
        btn.clicked.connect(self.on_browse)
        self.edit.textChanged.connect(self.on_changed)
        lay.addWidget(self.label)
        lay.addWidget(self.edit)
        lay.addWidget(btn)

    def path(self) -> Path:
        return Path(self.edit.text().strip())

    def setPath(self, p: Path):
        self.edit.setText(str(p))

    def on_browse(self):
        start_dir = str(self.path()) if self.edit.text().strip() else str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Escolher pasta", start_dir)
        if d:
            self.setPath(Path(d))

    def on_changed(self, *_):
        self.changed.emit(self.path())


class MkxTab(QWidget):
    def __init__(self):
        super().__init__()
        self.scan_thread: Optional[ScanMkvsThread] = None
        self.proc_thread: Optional[ProcessMkvsThread] = None

        root = QVBoxLayout(self)
        # Folders
        self.in_pick = FolderPicker("Pasta de entrada:", SRTDEF_PATH)
        self.out_pick = FolderPicker("Pasta de saída:")

        # Table
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Arquivo", "Tamanho", "Duração (s)", "Legendas (lang:codec)"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        # Controls
        ctrl = QHBoxLayout()
        self.format_cb = QComboBox(); self.format_cb.addItems(SUB_FORMATS)
        self.enc_cb = QComboBox(); self.enc_cb.addItems(list(TEXT_ENCODINGS.keys()))
        self.overwrite_cb = QCheckBox("Sobrescrever arquivos de saída")
        self.scan_btn = QPushButton("Ler diretório")
        self.go_btn = QPushButton("Executar")
        self.progress = QProgressBar(); self.progress.setValue(0)

        ctrl.addWidget(QLabel("Formato:")); ctrl.addWidget(self.format_cb)
        ctrl.addWidget(QLabel("Codificação:")); ctrl.addWidget(self.enc_cb)
        ctrl.addWidget(self.overwrite_cb)
        ctrl.addStretch(1)
        ctrl.addWidget(self.scan_btn)
        ctrl.addWidget(self.go_btn)

        root.addWidget(self.in_pick)
        root.addWidget(self.out_pick)
        root.addWidget(self.table)
        root.addLayout(ctrl)
        root.addWidget(self.progress)

        self.scan_btn.clicked.connect(self.on_scan)
        self.go_btn.clicked.connect(self.on_go)

    def on_scan(self):
        folder = self.in_pick.path()
        if not folder.exists():
            QMessageBox.warning(self, "Atenção", "Pasta de entrada não existe.")
            return
        self.table.setRowCount(0)
        self.scan_thread = ScanMkvsThread(folder)
        self.scan_thread.itemsReady.connect(self.populate)
        self.scan_thread.error.connect(lambda m: QMessageBox.critical(self, "Erro", m))
        self.scan_thread.start()

    def populate(self, items: List[MkxItem]):
        self.table.setRowCount(len(items))
        for i, it in enumerate(items):
            self.table.setItem(i, 0, QTableWidgetItem(it.path.name))
            self.table.setItem(i, 1, QTableWidgetItem(file_size_str(it.size)))
            self.table.setItem(i, 2, QTableWidgetItem(f"{it.duration:.2f}"))
            self.table.setItem(i, 3, QTableWidgetItem(it.subs_summary))

    def on_go(self):
        in_dir = self.in_pick.path()
        out_dir = self.out_pick.path()
        if not in_dir.exists():
            QMessageBox.warning(self, "Atenção", "Pasta de entrada não existe.")
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        target_fmt = self.format_cb.currentText()
        target_enc = self.enc_cb.currentText()
        overwrite = self.overwrite_cb.isChecked()

        self.proc_thread = ProcessMkvsThread(in_dir, out_dir, target_fmt, target_enc, overwrite)
        self.proc_thread.progress.connect(self.on_progress)
        self.proc_thread.error.connect(lambda m: QMessageBox.warning(self, "Aviso", m))
        self.proc_thread.done.connect(lambda m: QMessageBox.information(self, "Fim", m))
        self.proc_thread.start()

    def on_progress(self, val: int, msg: str):
        self.progress.setValue(val)
        self.progress.setFormat(f"{val}% - {msg}")


class SubsTab(QWidget):
    def __init__(self):
        super().__init__()
        self.scan_thread: Optional[ScanSubsThread] = None
        self.proc_thread: Optional[ProcessSubsThread] = None

        root = QVBoxLayout(self)
        self.in_pick = FolderPicker("Pasta de origem:", SRTDEF_PATH)
        self.out_pick = FolderPicker("Pasta de saída:")

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Arquivo", "Extensão", "Codificação detectada"]) 
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        ctrl = QHBoxLayout()
        self.format_cb = QComboBox(); self.format_cb.addItems(SUB_FORMATS)
        self.enc_cb = QComboBox(); self.enc_cb.addItems(list(TEXT_ENCODINGS.keys()))
        self.overwrite_cb = QCheckBox("Sobrescrever arquivos de saída")
        self.scan_btn = QPushButton("Ler diretório")
        self.go_btn = QPushButton("Executar")
        self.progress = QProgressBar(); self.progress.setValue(0)

        ctrl.addWidget(QLabel("Formato de saída:")); ctrl.addWidget(self.format_cb)
        ctrl.addWidget(QLabel("Codificação:")); ctrl.addWidget(self.enc_cb)
        ctrl.addWidget(self.overwrite_cb)
        ctrl.addStretch(1)
        ctrl.addWidget(self.scan_btn)
        ctrl.addWidget(self.go_btn)

        root.addWidget(self.in_pick)
        root.addWidget(self.out_pick)
        root.addWidget(self.table)
        root.addLayout(ctrl)
        root.addWidget(self.progress)

        self.scan_btn.clicked.connect(self.on_scan)
        self.go_btn.clicked.connect(self.on_go)

    def on_scan(self):
        folder = self.in_pick.path()
        if not folder.exists():
            QMessageBox.warning(self, "Atenção", "Pasta de origem não existe.")
            return
        self.table.setRowCount(0)
        self.scan_thread = ScanSubsThread(folder)
        self.scan_thread.itemsReady.connect(self.populate)
        self.scan_thread.error.connect(lambda m: QMessageBox.critical(self, "Erro", m))
        self.scan_thread.start()

    def populate(self, rows: List[Tuple[Path, str, str]]):
        self.table.setRowCount(len(rows))
        for i, (p, ext, enc) in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(p.name))
            self.table.setItem(i, 1, QTableWidgetItem(ext))
            self.table.setItem(i, 2, QTableWidgetItem(enc))

    def on_go(self):
        in_dir = self.in_pick.path()
        out_dir = self.out_pick.path()
        if not in_dir.exists():
            QMessageBox.warning(self, "Atenção", "Pasta de origem não existe.")
            return
        out_dir.mkdir(parents=True, exist_ok=True)
        target_fmt = self.format_cb.currentText()
        target_enc = self.enc_cb.currentText()
        overwrite = self.overwrite_cb.isChecked()

        self.proc_thread = ProcessSubsThread(in_dir, out_dir, target_fmt, target_enc, overwrite)
        self.proc_thread.progress.connect(self.on_progress)
        self.proc_thread.error.connect(lambda m: QMessageBox.warning(self, "Aviso", m))
        self.proc_thread.done.connect(lambda m: QMessageBox.information(self, "Fim", m))
        self.proc_thread.start()

    def on_progress(self, val: int, msg: str):
        self.progress.setValue(val)
        self.progress.setFormat(f"{val}% - {msg}")


# =============================
# Main Window
# =============================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sub Extract & Convert - PyQt6")
        self.resize(1100, 650)
        self._apply_dark_theme()

        tabs = QTabWidget()
        tabs.addTab(MkxTab(), "Extração + Conversão (MKV)")
        tabs.addTab(SubsTab(), "Somente Conversão (Legendas)")
        self.setCentralWidget(tabs)

        # Menu actions
        act_about = QAction("Sobre", self)
        act_about.triggered.connect(self.show_about)
        self.menuBar().addAction(act_about)

    def show_about(self):
        QMessageBox.information(self, "Sobre", (
            "Ferramenta para listar MKVs, extrair legendas e converter formatos/codificações.\n"
            "Requisitos: ffmpeg/ffprobe e MKVToolNix (mkvextract).\n"
            "Observação: PGS/DVD (bitmap) não são convertidos para texto (SRT/ASS/SSA)."
        ))

    def _apply_dark_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(30, 32, 34))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(230, 230, 230))
        palette.setColor(QPalette.ColorRole.Base, QColor(24, 26, 27))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(36, 38, 40))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
        palette.setColor(QPalette.ColorRole.Text, QColor(230, 230, 230))
        palette.setColor(QPalette.ColorRole.Button, QColor(45, 47, 50))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(230, 230, 230))
        palette.setColor(QPalette.ColorRole.BrightText, QColor(255, 0, 0))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(64, 128, 255))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        self.setPalette(palette)
        self.setStyleSheet("""
            QWidget { font-size: 12px; }
            QLineEdit, QComboBox { padding: 6px; border: 1px solid #555; border-radius: 6px; }
            QPushButton { padding: 8px 12px; border-radius: 8px; background: #3b82f6; color: white; }
            QPushButton:hover { background: #2563eb; }
            QTableWidget { gridline-color: #444; }
            QHeaderView::section { background: #2b2d30; padding: 6px; border: none; }
            QProgressBar { border: 1px solid #555; border-radius: 6px; text-align: center; }
            QProgressBar::chunk { background-color: #10b981; }
        """)


def check_binaries() -> Optional[str]:
    missing = []
    for name, path in {
        "ffmpeg": FFMPEG_PATH,
        "ffprobe": FFPROBE_PATH,
        "mkvextract": MKVEXTRACT_PATH,
        "mkvinfo": MKVINFO_PATH,
    }.items():
        if not Path(path).exists():
            missing.append(f"{name}: {path}")
    if missing:
        return "Ferramentas ausentes:\n" + "\n".join(missing)
    return None


def main():
    app = QApplication(sys.argv)
    warn = check_binaries()
    w = MainWindow()
    if warn:
        QMessageBox.warning(w, "Aviso", warn)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
