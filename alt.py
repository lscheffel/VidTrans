import os
import subprocess
import uuid
import shutil
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QCheckBox, QTableWidget, QTableWidgetItem, QMessageBox, QLineEdit, QComboBox
)
from PyQt6.QtGui import QPalette, QColor
from PyQt6.QtCore import Qt

FFMPEG_PATH = r"C:\Program Files\FFMPEG\bin\ffmpeg.exe"
FFPROBE_PATH = FFMPEG_PATH.replace("ffmpeg.exe", "ffprobe.exe")
MKVEXTRACT_PATH = r"C:\Program Files\MKVToolNix\mkvextract.exe"
TEMP_FOLDER = Path(r"E:\DB\TempSubs")

class SubtitleExtractor(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🎬 Extrator de Legendas MKV")
        self.resize(900, 600)
        self.files = []
        self.track_map = {}
        self.combo_boxes = {}

        self.init_ui()
        self.set_dark_theme()

    def init_ui(self):
        layout = QVBoxLayout()

        input_layout = QHBoxLayout()
        self.input_path = QLineEdit()
        input_button = QPushButton("Selecionar pasta de vídeos")
        input_button.clicked.connect(self.select_input_folder)
        input_layout.addWidget(QLabel("📁"))
        input_layout.addWidget(self.input_path)
        input_layout.addWidget(input_button)
        layout.addLayout(input_layout)

        output_layout = QHBoxLayout()
        self.output_path = QLineEdit()
        output_button = QPushButton("Selecionar pasta de saída")
        output_button.clicked.connect(self.select_output_folder)
        output_layout.addWidget(QLabel("📂"))
        output_layout.addWidget(self.output_path)
        output_layout.addWidget(output_button)
        layout.addLayout(output_layout)

        self.save_in_source = QCheckBox("Salvar na pasta de origem")
        self.overwrite_files = QCheckBox("Sobrescrever arquivos existentes")
        layout.addWidget(self.save_in_source)
        layout.addWidget(self.overwrite_files)

        default_layout = QHBoxLayout()
        self.default_track = QLineEdit()
        apply_default_button = QPushButton("Aplicar faixa padrão")
        apply_default_button.clicked.connect(self.apply_default_track)
        default_layout.addWidget(QLabel("🎯 Faixa padrão:"))
        default_layout.addWidget(self.default_track)
        default_layout.addWidget(apply_default_button)
        layout.addLayout(default_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["Arquivo", "Faixas", "Selecionar faixa"])
        layout.addWidget(self.table)

        extract_button = QPushButton("🚀 Extrair Legendas")
        extract_button.clicked.connect(self.run_extraction)
        layout.addWidget(extract_button)

        self.setLayout(layout)

    def set_dark_theme(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#2b2b2b"))
        palette.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Base, QColor("#3c3f41"))
        palette.setColor(QPalette.ColorRole.Text, Qt.GlobalColor.white)
        palette.setColor(QPalette.ColorRole.Button, QColor("#555555"))
        palette.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
        self.setPalette(palette)

    def select_input_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta de vídeos")
        if folder:
            self.input_path.setText(folder)
            self.load_files(folder)

    def select_output_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecionar pasta de saída")
        if folder:
            self.output_path.setText(folder)

    def load_files(self, folder):
        self.files.clear()
        self.track_map.clear()
        self.combo_boxes.clear()
        self.table.setRowCount(0)

        for file in os.listdir(folder):
            if file.lower().endswith(".mkv"):
                full_path = os.path.normpath(os.path.join(folder, file))
                tracks = self.get_subtitle_tracks(full_path)
                self.files.append((file, full_path))
                self.track_map[file] = tracks

                row = self.table.rowCount()
                self.table.insertRow(row)
                self.table.setItem(row, 0, QTableWidgetItem(file))
                self.table.setItem(row, 1, QTableWidgetItem(", ".join([f"{i}:{l}" for i, l in tracks])))

                combo = QComboBox()
                for i, lang in tracks:
                    combo.addItem(f"{i}:{lang}")
                if combo.count() > 0:
                    combo.setCurrentIndex(0)
                self.table.setCellWidget(row, 2, combo)
                self.combo_boxes[file] = combo

    def get_subtitle_tracks(self, file_path):
        try:
            result = subprocess.run(
                [FFPROBE_PATH, "-v", "error", "-select_streams", "s",
                 "-show_entries", "stream=index:stream_tags=language",
                 "-of", "csv=p=0", file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            tracks = []
            for line in result.stdout.strip().split('\n'):
                parts = line.split(',')
                if len(parts) >= 2:
                    index = parts[0].strip()
                    lang = parts[1].strip()
                    tracks.append((index, lang))
            return tracks
        except Exception as e:
            print(f"[ERROR] ffprobe falhou em '{file_path}': {e}")
            return []

    def apply_default_track(self):
        default = self.default_track.text().strip()
        if not default:
            return
        for combo in self.combo_boxes.values():
            for i in range(combo.count()):
                if combo.itemText(i).startswith(default + ":"):
                    combo.setCurrentIndex(i)
                    break

    def get_unique_filename(self, base_path):
        if self.overwrite_files.isChecked() or not os.path.exists(base_path):
            return base_path
        stem, ext = os.path.splitext(base_path)
        for i in range(1, 100):
            new_path = f"{stem}_{i:02d}{ext}"
            if not os.path.exists(new_path):
                return new_path
        return base_path

    def force_copy(self, src, dst):
        try:
            if os.path.exists(dst):
                os.remove(dst)
            shutil.copy2(src, dst)
            os.remove(src)
            return True
        except Exception as e:
            print(f"[COPY FAIL] {src} → {dst}: {e}")
            return False

    def run_extraction(self):
        input_folder = Path(self.input_path.text().strip()).resolve()
        output_folder = Path(self.output_path.text().strip()).resolve()
        if self.save_in_source.isChecked():
            output_folder = input_folder

        output_folder.mkdir(parents=True, exist_ok=True)
        TEMP_FOLDER.mkdir(parents=True, exist_ok=True)

        for temp_file in TEMP_FOLDER.glob("*"):
            try:
                temp_file.unlink()
            except Exception as e:
                print(f"[WARN] Não foi possível apagar {temp_file.name}: {e}")

        print("\n[START] Iniciando extração...\n")

        for file, full_path in self.files:
            combo = self.combo_boxes[file]
            selected = combo.currentText()
            if not selected or ":" not in selected:
                print(f"[SKIP] {file}: faixa não selecionada.")
                continue

            track_id = selected.split(":")[0].strip()
            base_name = os.path.splitext(file)[0]
            temp_ass = TEMP_FOLDER / f"temp_{uuid.uuid4().hex[:8]}.ass"
            temp_srt = TEMP_FOLDER / f"temp_{uuid.uuid4().hex[:8]}.srt"
            final_srt_name = f"{base_name}.srt"
            final_srt = Path(self.get_unique_filename(str(output_folder / final_srt_name)))
            final_srt = Path(os.path.realpath(str(final_srt)))
            final_srt.parent.mkdir(parents=True, exist_ok=True)

            try:
                print(f"[EXTRACT] {file} - faixa {track_id}")
                subprocess.run([
                    MKVEXTRACT_PATH,
                    "tracks",
                    str(full_path),
                    f"{track_id}:{str(temp_ass)}"
                ], check=True)

                subprocess.run([
                    FFMPEG_PATH,
                    "-i", str(temp_ass),
                    str(temp_srt)
                ], check=True)

                temp_ass.unlink()
                self.force_copy(str(temp_srt), str(final_srt))
                print(f"[SUCCESS] {final_srt.name} criado.\n")

            except Exception as e:
                print(f"[FAIL] {file}: {e}\n")

        print("[DONE] Extração finalizada.\n")
        QMessageBox.information(self, "Concluído", "Extração de legendas finalizada.")

if __name__ == "__main__":
    app = QApplication([])
    extractor = SubtitleExtractor()
    extractor.show()
    app.exec()
