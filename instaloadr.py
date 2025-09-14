import sys
import os
import time
import sqlite3
from threading import Thread, Event
from PyQt6 import QtWidgets, QtCore
import instaloader

# --- Configurações ---
DB_FILE = "insta_downloader.db"
DOWNLOAD_PATH = "X:/Insta"
# Pasta padrão onde o Instaloader salva sessions no Windows:
DEFAULT_SESSION_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Instaloader")

# --- Banco de dados (só posts baixados) ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS downloaded_posts (
                        id INTEGER PRIMARY KEY,
                        profile TEXT,
                        shortcode TEXT UNIQUE)''')
    conn.commit()
    conn.close()

def post_downloaded(shortcode):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM downloaded_posts WHERE shortcode=?", (shortcode,))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def mark_post_downloaded(profile, shortcode):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO downloaded_posts (profile, shortcode) VALUES (?, ?)", (profile, shortcode))
    conn.commit()
    conn.close()

# --- Thread de download ---
class DownloadThread(Thread):
    def __init__(self, L, profile_name, pause_event, log_func, progress_func):
        super().__init__()
        self.L = L
        self.profile_name = profile_name
        self.pause_event = pause_event
        self.log_func = log_func
        self.progress_func = progress_func

    def run(self):
        try:
            profile = instaloader.Profile.from_username(self.L.context, self.profile_name)
        except Exception as e:
            self.log_func(f"Erro ao acessar perfil {self.profile_name}: {e}")
            return

        posts = list(profile.get_posts())
        total = len(posts)
        count = 0
        self.progress_func(0, total)

        for post in posts:
            if post.is_video:
                if post_downloaded(post.shortcode):
                    self.log_func(f"[{self.profile_name}] Já baixado: {post.shortcode}")
                    count += 1
                    self.progress_func(count, total)
                    continue

                self.log_func(f"[{self.profile_name}] Baixando: {post.shortcode}")
                try:
                    # download_post cria a pasta DOWNLOAD_PATH/{profile_name}
                    self.L.download_post(post, target=self.profile_name)
                    mark_post_downloaded(self.profile_name, post.shortcode)
                except Exception as e:
                    self.log_func(f"[{self.profile_name}] Erro ao baixar: {e}")

                count += 1
                self.progress_func(count, total)

                # Pausa total ~2s (20 * 0.1s), mas respeita pausa do usuário
                for _ in range(20):
                    if self.pause_event.is_set():
                        while self.pause_event.is_set():
                            time.sleep(0.5)
                    time.sleep(0.1)

# --- UI ---
class InstaDownloader(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instagram Reels Downloader (SESSION-ONLY)")
        self.setGeometry(150, 150, 900, 650)

        # Instaloader - sem login aqui; carregaremos session file com load_session_from_file
        self.L = instaloader.Instaloader(dirname_pattern=f"{DOWNLOAD_PATH}/{{target}}",
                                         download_videos=True,
                                         download_video_thumbnails=False)
        self.session_loaded = False
        self.session_user = None
        self.session_path = None

        # estados
        self.profiles = []
        self.threads = []
        self.pause_event = Event()

        # UI
        self.init_ui()

        # DB
        init_db()

        # tenta detectar sessions na pasta padrão
        self.detect_sessions_in_default_dir()

    def init_ui(self):
        layout = QtWidgets.QVBoxLayout()

        # --- Sessão ---
        sess_layout = QtWidgets.QHBoxLayout()
        self.session_label = QtWidgets.QLabel("Sessão: (nenhuma carregada)")
        self.btn_select_session = QtWidgets.QPushButton("Carregar sessão (.session)...")
        self.btn_select_session.clicked.connect(self.select_session_file)
        self.btn_detect = QtWidgets.QPushButton("Detectar sessions locais")
        self.btn_detect.clicked.connect(self.detect_sessions_in_default_dir)
        sess_layout.addWidget(self.session_label)
        sess_layout.addWidget(self.btn_select_session)
        sess_layout.addWidget(self.btn_detect)
        layout.addLayout(sess_layout)

        # lista de sessions detectadas (se houver)
        self.sessions_list = QtWidgets.QListWidget()
        self.sessions_list.setMaximumHeight(80)
        self.sessions_list.itemDoubleClicked.connect(self.load_selected_session_from_list)
        layout.addWidget(QtWidgets.QLabel("Sessions detectadas (duplo clique para carregar):"))
        layout.addWidget(self.sessions_list)

        # --- Perfis ---
        prof_layout = QtWidgets.QHBoxLayout()
        self.profile_input = QtWidgets.QLineEdit()
        self.profile_input.setPlaceholderText("Perfil a baixar (ex: fulano)")
        self.add_profile_btn = QtWidgets.QPushButton("Adicionar Perfil")
        self.add_profile_btn.clicked.connect(self.add_profile)
        self.remove_profile_btn = QtWidgets.QPushButton("Remover Selecionado")
        self.remove_profile_btn.clicked.connect(self.remove_selected_profile)
        prof_layout.addWidget(self.profile_input)
        prof_layout.addWidget(self.add_profile_btn)
        prof_layout.addWidget(self.remove_profile_btn)
        layout.addWidget(QtWidgets.QLabel("Perfis alvo:"))
        layout.addLayout(prof_layout)

        self.profiles_list = QtWidgets.QListWidget()
        self.profiles_list.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self.profiles_list)

        # --- Controle ---
        btn_layout = QtWidgets.QHBoxLayout()
        self.start_btn = QtWidgets.QPushButton("Iniciar Download")
        self.start_btn.clicked.connect(self.start_download)
        self.pause_btn = QtWidgets.QPushButton("Pausar")
        self.pause_btn.clicked.connect(self.pause_download)
        self.resume_btn = QtWidgets.QPushButton("Retomar")
        self.resume_btn.clicked.connect(self.resume_download)
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.pause_btn)
        btn_layout.addWidget(self.resume_btn)
        layout.addLayout(btn_layout)

        # --- Progresso ---
        self.progress_bar = QtWidgets.QProgressBar()
        layout.addWidget(self.progress_bar)

        # --- Log ---
        self.log = QtWidgets.QTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(QtWidgets.QLabel("Log detalhado:"))
        layout.addWidget(self.log)

        # instruções rápidas
        instr = QtWidgets.QLabel(
            "Instruções: gere a session com `instaloader -l seu_usuario` no mesmo PC. "
            "Coloque o arquivo session-<usuario> em: %s  (ou selecione manualmente)." % DEFAULT_SESSION_DIR
        )
        instr.setWordWrap(True)
        layout.addWidget(instr)

        self.setLayout(layout)

    # ---------- sessões ----------
    def detect_sessions_in_default_dir(self):
        self.sessions_list.clear()
        if not os.path.isdir(DEFAULT_SESSION_DIR):
            self.log_message(f"Pasta padrão de sessions não encontrada: {DEFAULT_SESSION_DIR}")
            return

        files = os.listdir(DEFAULT_SESSION_DIR)
        sessions = [f for f in files if f.startswith("session-")]
        if not sessions:
            self.log_message(f"Nenhuma session encontrada em {DEFAULT_SESSION_DIR}")
            return

        for s in sessions:
            item = QtWidgets.QListWidgetItem(s)
            self.sessions_list.addItem(item)
        self.log_message(f"Encontradas {len(sessions)} session(s) em {DEFAULT_SESSION_DIR}")

    def select_session_file(self):
        fname, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Selecione arquivo .session", DEFAULT_SESSION_DIR, "Session files (*)")
        if not fname:
            return
        self.load_session_from_path(fname)

    def load_selected_session_from_list(self, item):
        fname = os.path.join(DEFAULT_SESSION_DIR, item.text())
        self.load_session_from_path(fname)

    def load_session_from_path(self, path):
        # tenta inferir o usuário a partir do nome do arquivo (session-<user>)
        basename = os.path.basename(path)
        if not basename.startswith("session-"):
            QtWidgets.QMessageBox.warning(self, "Arquivo inválido", "O arquivo selecionado não parece ser um session do Instaloader (deve começar com 'session-').")
            return

        # extrai usuário
        user = basename[len("session-"):]
        try:
            self.L.load_session_from_file(user, filename=path)
            self.session_loaded = True
            self.session_user = user
            self.session_path = path
            self.session_label.setText(f"Sessão carregada: {basename}")
            self.log_message(f"Session carregada: {path} (usuário: {user})")
        except Exception as e:
            self.session_loaded = False
            self.session_user = None
            self.session_path = None
            self.session_label.setText("Sessão: (nenhuma carregada)")
            self.log_message(f"Erro ao carregar session {path}: {e}")
            QtWidgets.QMessageBox.critical(self, "Erro ao carregar session", f"Erro: {e}")

    # ---------- perfis ----------
    def add_profile(self):
        profile = self.profile_input.text().strip()
        if profile:
            if profile in self.profiles:
                self.log_message(f"Perfil já na lista: {profile}")
            else:
                self.profiles.append(profile)
                self.profiles_list.addItem(profile)
                self.profile_input.clear()

    def remove_selected_profile(self):
        row = self.profiles_list.currentRow()
        if row >= 0:
            item = self.profiles_list.takeItem(row)
            profile = item.text()
            try:
                self.profiles.remove(profile)
            except ValueError:
                pass

    # ---------- progresso / log ----------
    def log_message(self, msg):
        timestamp = QtCore.QDateTime.currentDateTime().toString("yyyy-MM-dd HH:mm:ss")
        self.log.append(f"[{timestamp}] {msg}")
        QtWidgets.QApplication.processEvents()

    def progress_update(self, value, maximum):
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)

    # ---------- controle de download ----------
    def start_download(self):
        if not self.session_loaded:
            QtWidgets.QMessageBox.warning(self, "Sessão não carregada", "Por favor carregue uma sessão `.session` antes de iniciar o download.")
            return
        if not self.profiles:
            QtWidgets.QMessageBox.warning(self, "Sem perfis", "Adicione ao menos um perfil alvo.")
            return

        # reset progress bar
        self.progress_bar.setValue(0)
        self.pause_event.clear()
        self.threads = []

        # inicia uma thread por perfil (threads controladas, cada thread serializa seus próprios downloads)
        for profile_name in self.profiles:
            thread = DownloadThread(self.L, profile_name, self.pause_event, self.log_message, self.progress_update)
            thread.start()
            self.threads.append(thread)
            self.log_message(f"Thread iniciada para {profile_name}")

    def pause_download(self):
        self.pause_event.set()
        self.log_message("Download pausado pelo usuário.")

    def resume_download(self):
        self.pause_event.clear()
        self.log_message("Download retomado pelo usuário.")

# --- Main ---
def main():
    app = QtWidgets.QApplication(sys.argv)
    window = InstaDownloader()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
