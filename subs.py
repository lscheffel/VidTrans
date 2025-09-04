import sys
from pathlib import Path
import subprocess
import json
import os
import chardet
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QListWidget, QComboBox, QCheckBox, QMessageBox, QFileDialog
)
from PyQt6.QtCore import Qt

FFMPEG_PATH = r"C:\Program Files\FFMPEG\bin\ffmpeg.exe"
FFPROBE_PATH = r"C:\Program Files\FFMPEG\bin\ffprobe.exe"
MKVEXTRACT_PATH = r"C:\Program Files\MKVToolNix\mkvextract.exe"
MKVINFO_PATH = r"C:\Program Files\MKVToolNix\mkvinfo.exe"
MKVMERGE_PATH = r"C:\Program Files\MKVToolNix\mkvmerge.exe"  # Added for JSON info
SRTDEF_PATH = Path(r"C:\subtitles")
TEMP_FOLDER = Path(r"E:\DB\TempSubs")

class SubtitleApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Subtitle Extractor & Converter")
        self.setGeometry(100, 100, 800, 600)
        
        # Dark theme
        self.setStyleSheet("""
            QWidget { background-color: #2b2b2b; color: #f0f0f0; }
            QPushButton { background-color: #4CAF50; color: #f0f0f0; border: 1px solid #3e3e3e; }
            QPushButton:hover { background-color: #45a049; }
            QLineEdit { background-color: #3c3c3c; color: #f0f0f0; border: 1px solid #555; }
            QListWidget { background-color: #3c3c3c; color: #f0f0f0; border: 1px solid #555; }
            QComboBox { background-color: #3c3c3c; color: #f0f0f0; border: 1px solid #555; }
            QComboBox QAbstractItemView { background-color: #3c3c3c; color: #f0f0f0; }
            QCheckBox { color: #f0f0f0; }
            QTabWidget::pane { border: 1px solid #555; background-color: #2b2b2b; }
            QTabBar::tab { background-color: #3c3c3c; color: #f0f0f0; padding: 8px; }
            QTabBar::tab:selected { background-color: #4CAF50; }
        """)
        
        self.tab_widget = QTabWidget()
        self.setCentralWidget(self.tab_widget)
        
        self.tab1 = QWidget()
        self.tab2 = QWidget()
        self.tab_widget.addTab(self.tab1, "Extract & Convert")
        self.tab_widget.addTab(self.tab2, "Convert Only")
        
        self.setup_tab1()
        self.setup_tab2()

    def setup_tab1(self):
        layout = QVBoxLayout()
        
        # Input folder
        input_layout = QHBoxLayout()
        input_label = QLabel("Pasta de Entrada:")
        self.input_folder1 = QLineEdit(str(SRTDEF_PATH))
        browse_input = QPushButton("Procurar")
        browse_input.clicked.connect(self.browse_input1)
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.input_folder1)
        input_layout.addWidget(browse_input)
        layout.addLayout(input_layout)
        
        # Output folder
        output_layout = QHBoxLayout()
        output_label = QLabel("Pasta de Saída:")
        self.output_folder1 = QLineEdit(str(TEMP_FOLDER))
        browse_output = QPushButton("Procurar")
        browse_output.clicked.connect(self.browse_output1)
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_folder1)
        output_layout.addWidget(browse_output)
        layout.addLayout(output_layout)
        
        # Video list
        self.video_list = QListWidget()
        layout.addWidget(self.video_list)
        
        # Dropdowns
        format_layout = QHBoxLayout()
        format_label = QLabel("Formato de Saída:")
        self.format_combo1 = QComboBox()
        self.format_combo1.addItems(["srt", "ass", "ssa", "mks"])
        encoding_label = QLabel("Codificação:")
        self.encoding_combo1 = QComboBox()
        self.encoding_combo1.addItems(["UTF-8 (sem BOM)", "UTF-8 +BOM", "ANSI Latin I (1252)", "UTF-7"])
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_combo1)
        format_layout.addWidget(encoding_label)
        format_layout.addWidget(self.encoding_combo1)
        layout.addLayout(format_layout)
        
        # Checkbox
        self.overwrite1 = QCheckBox("Sobrescrever arquivos existentes")
        layout.addWidget(self.overwrite1)
        
        # Buttons
        buttons_layout = QHBoxLayout()
        load_btn = QPushButton("Carregar Vídeos")
        load_btn.clicked.connect(self.load_videos)
        execute_btn = QPushButton("Executar")
        execute_btn.clicked.connect(self.execute_tab1)
        buttons_layout.addWidget(load_btn)
        buttons_layout.addWidget(execute_btn)
        layout.addLayout(buttons_layout)
        
        self.tab1.setLayout(layout)

    def setup_tab2(self):
        layout = QVBoxLayout()
        
        # Input folder
        input_layout = QHBoxLayout()
        input_label = QLabel("Pasta de Entrada:")
        self.input_folder2 = QLineEdit(str(SRTDEF_PATH))
        browse_input = QPushButton("Procurar")
        browse_input.clicked.connect(self.browse_input2)
        input_layout.addWidget(input_label)
        input_layout.addWidget(self.input_folder2)
        input_layout.addWidget(browse_input)
        layout.addLayout(input_layout)
        
        # Output folder
        output_layout = QHBoxLayout()
        output_label = QLabel("Pasta de Saída:")
        self.output_folder2 = QLineEdit(str(TEMP_FOLDER))
        browse_output = QPushButton("Procurar")
        browse_output.clicked.connect(self.browse_output2)
        output_layout.addWidget(output_label)
        output_layout.addWidget(self.output_folder2)
        output_layout.addWidget(browse_output)
        layout.addLayout(output_layout)
        
        # Subtitle list
        self.sub_list = QListWidget()
        layout.addWidget(self.sub_list)
        
        # Dropdowns
        format_layout = QHBoxLayout()
        format_label = QLabel("Formato de Saída:")
        self.format_combo2 = QComboBox()
        self.format_combo2.addItems(["srt", "ass", "ssa", "mks"])
        encoding_label = QLabel("Codificação:")
        self.encoding_combo2 = QComboBox()
        self.encoding_combo2.addItems(["UTF-8 (sem BOM)", "UTF-8 +BOM", "ANSI Latin I (1252)", "UTF-7"])
        format_layout.addWidget(format_label)
        format_layout.addWidget(self.format_combo2)
        format_layout.addWidget(encoding_label)
        format_layout.addWidget(self.encoding_combo2)
        layout.addLayout(format_layout)
        
        # Checkbox
        self.overwrite2 = QCheckBox("Sobrescrever arquivos existentes")
        layout.addWidget(self.overwrite2)
        
        # Buttons
        buttons_layout = QHBoxLayout()
        load_btn = QPushButton("Carregar Legendas")
        load_btn.clicked.connect(self.load_subs)
        execute_btn = QPushButton("Executar")
        execute_btn.clicked.connect(self.execute_tab2)
        buttons_layout.addWidget(load_btn)
        buttons_layout.addWidget(execute_btn)
        layout.addLayout(buttons_layout)
        
        self.tab2.setLayout(layout)

    def browse_input1(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Entrada")
        if folder:
            self.input_folder1.setText(folder)

    def browse_output1(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Saída")
        if folder:
            self.output_folder1.setText(folder)

    def browse_input2(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Entrada")
        if folder:
            self.input_folder2.setText(folder)

    def browse_output2(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Saída")
        if folder:
            self.output_folder2.setText(folder)

    def load_videos(self):
        input_path = Path(self.input_folder1.text())
        self.video_list.clear()
        for file in input_path.glob("*.mkv"):
            size = os.path.getsize(file) / (1024 * 1024)
            duration = self.get_duration(file)
            subs_count = self.get_subs_count(file)
            info = f"{file.name} | Tamanho: {size:.2f} MB | Duração: {duration} | Legendas: {subs_count}"
            self.video_list.addItem(info)

    def get_duration(self, file):
        try:
            result = subprocess.run([FFPROBE_PATH, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file)], capture_output=True, text=True)
            return f"{float(result.stdout):.2f} s"
        except:
            return "Desconhecido"

    def get_subs_count(self, file):
        try:
            result = subprocess.run([MKVMERGE_PATH, "-J", str(file)], capture_output=True, text=True)
            info = json.loads(result.stdout)
            return len([t for t in info['tracks'] if t['type'] == 'subtitles'])
        except:
            return 0

    def get_first_sub_info(self, file):
        try:
            result = subprocess.run([MKVMERGE_PATH, "-J", str(file)], capture_output=True, text=True)
            info = json.loads(result.stdout)
            for t in info['tracks']:
                if t['type'] == 'subtitles':
                    codec = t['codec'].lower()
                    ext = 'srt' if 'subrip' in codec else 'ass' if 'ass' in codec else 'ssa' if 'ssa' in codec else 'mks'
                    return t['id'], ext
            return None, None
        except:
            return None, None

    def execute_tab1(self):
        input_path = Path(self.input_folder1.text())
        output_path = Path(self.output_folder1.text())
        out_format = self.format_combo1.currentText()
        out_encoding_text = self.encoding_combo1.currentText()
        out_encoding = self.get_encoding_code(out_encoding_text)
        overwrite = self.overwrite1.isChecked()
        
        os.makedirs(TEMP_FOLDER, exist_ok=True)
        
        for i in range(self.video_list.count()):
            item = self.video_list.item(i)
            file_name = item.text().split(" | ")[0]
            mkv_file = input_path / file_name
            sub_file = output_path / f"{mkv_file.stem}.{out_format}"
            
            if not overwrite and sub_file.exists():
                sub_file = output_path / f"{mkv_file.stem}_new.{out_format}"
            
            track_id, orig_ext = self.get_first_sub_info(mkv_file)
            if track_id is None:
                continue
            
            temp_sub = TEMP_FOLDER / f"{mkv_file.stem}.{orig_ext}"
            subprocess.run([MKVEXTRACT_PATH, "tracks", str(mkv_file), f"{track_id}:{temp_sub}"])
            
            self.convert_sub(temp_sub, sub_file, out_format, out_encoding)
            
            os.remove(temp_sub)
        
        QMessageBox.information(self, "Concluído", "Extração e conversão concluídas.")

    def load_subs(self):
        input_path = Path(self.input_folder2.text())
        self.sub_list.clear()
        extensions = [".srt", ".ass", ".ssa", ".mks"]
        for file in input_path.iterdir():
            if file.is_file() and file.suffix.lower() in extensions:
                encoding = self.detect_encoding(file) or "Desconhecido"
                info = f"{file.name} | Extensão: {file.suffix} | Codificação: {encoding}"
                self.sub_list.addItem(info)

    def detect_encoding(self, file):
        try:
            with open(file, 'rb') as f:
                raw = f.read(10000)  # Read partial for speed
                result = chardet.detect(raw)
                return result['encoding']
        except:
            return None

    def execute_tab2(self):
        input_path = Path(self.input_folder2.text())
        output_path = Path(self.output_folder2.text())
        out_format = self.format_combo2.currentText()
        out_encoding_text = self.encoding_combo2.currentText()
        out_encoding = self.get_encoding_code(out_encoding_text)
        overwrite = self.overwrite2.isChecked()
        
        os.makedirs(TEMP_FOLDER, exist_ok=True)
        
        for i in range(self.sub_list.count()):
            item = self.sub_list.item(i)
            file_name = item.text().split(" | ")[0]
            sub_file_in = input_path / file_name
            sub_file_out = output_path / f"{sub_file_in.stem}.{out_format}"
            
            if not overwrite and sub_file_out.exists():
                sub_file_out = output_path / f"{sub_file_in.stem}_new.{out_format}"
            
            self.convert_sub(sub_file_in, sub_file_out, out_format, out_encoding)
        
        QMessageBox.information(self, "Concluído", "Conversão concluída.")

    def convert_sub(self, input_sub, output_sub, out_format, out_encoding):
        temp_conv = TEMP_FOLDER / f"conv.{out_format}"
        subprocess.run([FFMPEG_PATH, "-i", str(input_sub), str(temp_conv)], capture_output=True)
        
        src_enc = self.detect_encoding(temp_conv) or 'utf-8'
        try:
            with open(temp_conv, 'r', encoding=src_enc) as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(temp_conv, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
        
        with open(output_sub, 'w', encoding=out_encoding) as f:
            f.write(content)
        
        os.remove(temp_conv)

    def get_encoding_code(self, enc_text):
        if "UTF-8 (sem BOM)" in enc_text:
            return 'utf-8'
        elif "UTF-8 +BOM" in enc_text:
            return 'utf-8-sig'
        elif "ANSI Latin I (1252)" in enc_text:
            return 'windows-1252'
        elif "UTF-7" in enc_text:
            return 'utf-7'
        return 'utf-8'

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SubtitleApp()
    window.show()
    sys.exit(app.exec())