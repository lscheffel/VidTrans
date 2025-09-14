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
    def __init__(self, L, profile_name, options, pause_event, log_func, progress_func):
        super().__init__()
        self.L = L
        self.profile_name = profile_name
        self.options = options  # dict com chaves: reels, feed, stories, highlights, igtv, tagged
        self.pause_event = pause_event
        self.log_func = log_func
        self.progress_func = progress_func

    def run(self):
        try:
            profile = instaloader.Profile.from_username(self.L.context, self.profile_name)
        except Exception as e:
            self.log_func(f"Erro ao acessar perfil {self.profile_name}: {e}")
            return

        # Reunir conjuntos para evitar duplicação
        to_download_posts = []  # lista de Post objects para baixar via L.download_post
        to_download_shortcodes = set()

        total_estimated = 0
        counted_sources = []

        # FEED (posts comuns)
        if self.options.get("feed", False):
            try:
                posts_feed = list(profile.get_posts())
                counted_sources.append(("feed", posts_feed))
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao listar feed: {e}")
                posts_feed = []

        else:
            posts_feed = []

        # IGTV
        posts_igtv = []
        if self.options.get("igtv", False):
            try:
                # get_igtv_posts may not be available depending on instaloader version
                posts_igtv = list(profile.get_igtv_posts())
                counted_sources.append(("igtv", posts_igtv))
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao listar IGTV: {e}")

        # TAGGED
        posts_tagged = []
        if self.options.get("tagged", False):
            try:
                posts_tagged = list(profile.get_tagged_posts())
                counted_sources.append(("tagged", posts_tagged))
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao listar marcados (tagged): {e}")

        # REELS - usamos posts_feed (reels são videos dentro do feed on many accounts)
        posts_reels = []
        if self.options.get("reels", False):
            # prefer filter from feed if available, otherwise iterate posts
            try:
                if not posts_feed:
                    posts_feed = list(profile.get_posts())
                    counted_sources.append(("feed_for_reels", posts_feed))
                posts_reels = [p for p in posts_feed if p.is_video]
                counted_sources.append(("reels", posts_reels))
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao listar reels: {e}")

        # STORIES - Instaloader method downloads directly; we will count items if possible
        story_items = []
        if self.options.get("stories", False):
            try:
                # get_stories returns a generator of stories objects (per user)
                stories_gen = self.L.get_stories(userids=[profile.userid])
                # convert to list of story items to count and download manually via download_storyitem
                for story in stories_gen:
                    for item in story.get_items():
                        story_items.append(item)
                total_estimated += len(story_items)
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao obter stories: {e}")
                story_items = []

        # HIGHLIGHTS - download_highlight requires highlight object; attempt to iterate
        highlight_items_count = 0
        highlights = []
        if self.options.get("highlights", False):
            try:
                # profile.get_highlights() yields Highlight objects (may require instaloader version)
                try:
                    highlights = list(profile.get_highlights())
                except Exception:
                    highlights = []
                # You can download highlights via L.download_highlight(highlight)
                # We'll attempt to just count items by iterating highlight.get_items()
                for h in highlights:
                    try:
                        items = list(h.get_items())
                        highlight_items_count += len(items)
                    except Exception:
                        # fallback: unknown count
                        pass
                total_estimated += highlight_items_count
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao listar highlights: {e}")
                highlights = []

        # Build unified set of posts to download (feed, igtv, tagged, reels)
        # Use shortcodes to avoid duplicates
        for source_name, posts in counted_sources:
            for p in posts:
                sc = getattr(p, "shortcode", None)
                if not sc:
                    continue
                if sc not in to_download_shortcodes:
                    to_download_shortcodes.add(sc)
                    to_download_posts.append(p)

        # Now total_estimated includes posts + story + highlights
        total_estimated += len(to_download_posts)

        # Initialize progress
        downloaded_count = 0
        already_existing_count = 0
        self.progress_func(0, total_estimated if total_estimated > 0 else 1)

        # First: download posts (feed/igtv/tagged/reels unified)
        for post in to_download_posts:
            sc = getattr(post, "shortcode", None)
            if not sc:
                continue

            if post_downloaded(sc):
                self.log_func(f"[{self.profile_name}] Já baixado: {sc}")
                already_existing_count += 1
                downloaded_count += 1
                self.progress_func(downloaded_count, total_estimated)
                # small pause but respectful
                for _ in range(5):
                    if self.pause_event.is_set():
                        while self.pause_event.is_set():
                            time.sleep(0.5)
                    time.sleep(0.05)
                continue

            self.log_func(f"[{self.profile_name}] Baixando post: {sc}")
            try:
                self.L.download_post(post, target=self.profile_name)
                mark_post_downloaded(self.profile_name, sc)
                downloaded_count += 1
            except Exception as e:
                self.log_func(f"[{self.profile_name}] Erro ao baixar post {sc}: {e}")
                downloaded_count += 1  # conta mesmo que falhe para não travar progresso

            self.progress_func(downloaded_count, total_estimated)

            # Pausa total ~2s (dividida)
            for _ in range(20):
                if self.pause_event.is_set():
                    while self.pause_event.is_set():
                        time.sleep(0.5)
                time.sleep(0.1)

        # Then: download stories (already counted earlier)
        if self.options.get("stories", False) and story_items:
            for item in story_items:
                # story items don't have shortcode; fall back to unique id
                unique_id = getattr(item, "pk", None) or getattr(item, "id", None)
                id_label = unique_id or "story_item"
                # We won't store story items in DB because they don't have shortcodes.
                self.log_func(f"[{self.profile_name}] Baixando story item: {id_label}")
                try:
                    self.L.download_storyitem(item, target=self.profile_name)
                except Exception as e:
                    # Some instaloader versions may not expose download_storyitem; fallback to download_stories
                    self.log_func(f"[{self.profile_name}] Erro ao baixar story item: {e} (tentando download_stories...)")
                    try:
                        self.L.download_stories(userids=[profile.userid], filename_target=self.profile_name)
                        # if download_stories works, we can't know exact count; mark progress conservatively
                    except Exception as e2:
                        self.log_func(f"[{self.profile_name}] Erro no download_stories: {e2}")
                downloaded_count += 1
                self.progress_func(downloaded_count, total_estimated)
                # small pause
                for _ in range(10):
                    if self.pause_event.is_set():
                        while self.pause_event.is_set():
                            time.sleep(0.5)
                    time.sleep(0.1)

        # Then: download highlights (if any)
        if self.options.get("highlights", False) and highlights:
            for h in highlights:
                try:
                    self.log_func(f"[{self.profile_name}] Baixando highlight: {getattr(h, 'title', str(h))}")
                    # L.download_highlight expects a Highlight object or id depending on version
                    try:
                        self.L.download_highlight(h, target=self.profile_name)
                    except TypeError:
                        # fallback: try using id
                        try:
                            hid = getattr(h, "id", None)
                            if hid:
                                self.L.download_highlight(hid, target=self.profile_name)
                        except Exception as e:
                            self.log_func(f"[{self.profile_name}] Erro ao baixar highlight (fallback): {e}")
                    except Exception as e:
                        self.log_func(f"[{self.profile_name}] Erro ao baixar highlight: {e}")
                except Exception as e:
                    self.log_func(f"[{self.profile_name}] Erro processando highlight: {e}")

                # increment progress by number of items if we counted them; otherwise +1
                increment = 0
                try:
                    increment = len(list(h.get_items()))
                except Exception:
                    increment = 1
                downloaded_count += increment
                self.progress_func(downloaded_count, total_estimated)

                # pause
                for _ in range(10):
                    if self.pause_event.is_set():
                        while self.pause_event.is_set():
                            time.sleep(0.5)
                    time.sleep(0.1)

        # Final adjustments: if total_estimated was zero (nothing selected), set progress to 1 and mark done
        if total_estimated == 0:
            self.progress_func(1, 1)

        # Final log summary for this profile
        self.log_func(f"[{self.profile_name}] Concluído: {downloaded_count} processados ({already_existing_count} já existentes).")
        # ensure progress bar reaches maximum
        self.progress_func(total_estimated if total_estimated > 0 else 1, total_estimated if total_estimated > 0 else 1)


# --- UI ---
class InstaDownloader(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instagram Reels Downloader (SESSION-ONLY) - com opções")
        self.setGeometry(150, 150, 980, 720)

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

        # --- Opções de conteúdo (checkboxes) ---
        options_group = QtWidgets.QGroupBox("O que baixar (marque as opções desejadas)")
        options_layout = QtWidgets.QHBoxLayout()

        self.chk_reels = QtWidgets.QCheckBox("Reels (vídeos)")
        self.chk_feed = QtWidgets.QCheckBox("Feed (posts)")
        self.chk_stories = QtWidgets.QCheckBox("Stories")
        self.chk_highlights = QtWidgets.QCheckBox("Highlights")
        self.chk_igtv = QtWidgets.QCheckBox("IGTV")
        self.chk_tagged = QtWidgets.QCheckBox("Tagged (marcados)")

        # defaults: reels + feed checked
        self.chk_reels.setChecked(True)
        self.chk_feed.setChecked(True)

        options_layout.addWidget(self.chk_reels)
        options_layout.addWidget(self.chk_feed)
        options_layout.addWidget(self.chk_stories)
        options_layout.addWidget(self.chk_highlights)
        options_layout.addWidget(self.chk_igtv)
        options_layout.addWidget(self.chk_tagged)

        options_group.setLayout(options_layout)
        layout.addWidget(options_group)

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
        # chamada pelos threads para atualizar a barra
        self.progress_bar.setMaximum(maximum)
        self.progress_bar.setValue(value)
        QtWidgets.QApplication.processEvents()

    # ---------- controle de download ----------
    def start_download(self):
        if not self.session_loaded:
            QtWidgets.QMessageBox.warning(self, "Sessão não carregada", "Por favor carregue uma sessão `.session` antes de iniciar o download.")
            return
        if not self.profiles:
            QtWidgets.QMessageBox.warning(self, "Sem perfis", "Adicione ao menos um perfil alvo.")
            return

        # coletar opções
        options = {
            "reels": self.chk_reels.isChecked(),
            "feed": self.chk_feed.isChecked(),
            "stories": self.chk_stories.isChecked(),
            "highlights": self.chk_highlights.isChecked(),
            "igtv": self.chk_igtv.isChecked(),
            "tagged": self.chk_tagged.isChecked(),
        }

        # reset progress bar
        self.progress_bar.setValue(0)
        self.pause_event.clear()
        self.threads = []

        # inicia uma thread por perfil (cada thread faz seu próprio fluxo, mas usa a mesma sessão L)
        for profile_name in self.profiles:
            thread = DownloadThread(self.L, profile_name, options, self.pause_event, self.log_message, self.progress_update)
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
