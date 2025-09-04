## Conversor de Legendas SRT com Interface Gráfica em PyQt6
# Este script cria uma aplicação GUI para converter arquivos de legenda .srt entre diferentes codificações.
# Ele utiliza a biblioteca chardet para detectar a codificação original dos arquivos e permite ao usuário
# selecionar a codificação de saída desejada. O usuário pode escolher uma pasta de entrada contendo arquivos de legenda .srt     e converte-os para outras codificações.

# Produzido com o Gemini
# https://gemini.google.com/app/6808289df3c430a3

import os
import chardet
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QLabel, QLineEdit, QFileDialog, QTextEdit,
    QComboBox, QListWidget, QGroupBox, QHBoxLayout,
    QSplitter
)
from PyQt6.QtCore import Qt

class ConversorLegendas(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('Conversor de Legendas SRT')
        self.setGeometry(100, 100, 800, 500)
        self.init_ui()

    def init_ui(self):
        # Layout principal
        main_layout = QVBoxLayout()

        # Splitter para o diretório de entrada e o log/lista
        splitter_top = QSplitter(Qt.Orientation.Horizontal)
        
        # Grupo de entrada
        group_input = QGroupBox("Pasta de Entrada")
        layout_input = QVBoxLayout()
        
        self.input_dir = QLineEdit()
        self.input_dir.setPlaceholderText('Caminho para a pasta...')
        self.input_dir.setReadOnly(True)
        layout_input.addWidget(self.input_dir)

        btn_selecionar_dir = QPushButton('Selecionar Pasta')
        btn_selecionar_dir.clicked.connect(self.selecionar_diretorio)
        layout_input.addWidget(btn_selecionar_dir)
        
        group_input.setLayout(layout_input)
        splitter_top.addWidget(group_input)
        
        # Grupo de arquivos
        group_arquivos = QGroupBox("Arquivos Encontrados (.srt)")
        layout_arquivos = QVBoxLayout()
        self.list_arquivos = QListWidget()
        layout_arquivos.addWidget(self.list_arquivos)
        group_arquivos.setLayout(layout_arquivos)
        splitter_top.addWidget(group_arquivos)
        
        main_layout.addWidget(splitter_top)

        # Layout para codificação de saída e pasta de saída
        layout_options = QHBoxLayout()
        
        # Grupo de seleção de codificação
        group_codificacao = QGroupBox("Opções de Conversão")
        layout_codificacao = QVBoxLayout()
        
        label_codificacao = QLabel('Codificação de Saída:')
        self.combo_codificacao = QComboBox()
        self.combo_codificacao.addItems(['cp1252', 'utf-8', 'utf-16', 'utf-8-sig'])
        self.combo_codificacao.setCurrentText('cp1252')
        
        layout_codificacao.addWidget(label_codificacao)
        layout_codificacao.addWidget(self.combo_codificacao)
        group_codificacao.setLayout(layout_codificacao)
        layout_options.addWidget(group_codificacao)

        # Grupo para a pasta de saída
        group_output = QGroupBox("Pasta de Saída (Opcional)")
        layout_output = QVBoxLayout()
        
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText('Manter vazio para converter no local...')
        self.output_dir.setReadOnly(True)
        
        btn_selecionar_output = QPushButton('Selecionar Pasta de Saída')
        btn_selecionar_output.clicked.connect(self.selecionar_output_diretorio)

        layout_output.addWidget(self.output_dir)
        layout_output.addWidget(btn_selecionar_output)
        group_output.setLayout(layout_output)
        layout_options.addWidget(group_output)
        
        main_layout.addLayout(layout_options)

        # Botão de conversão
        self.btn_converter = QPushButton('Converter Legendas')
        self.btn_converter.clicked.connect(self.iniciar_conversao)
        main_layout.addWidget(self.btn_converter)

        # Área de log
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        main_layout.addWidget(self.log_area)

        self.setLayout(main_layout)

    def selecionar_diretorio(self):
        diretorio = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Legendas")
        if diretorio:
            self.input_dir.setText(diretorio)
            self.atualizar_lista_arquivos(diretorio)

    def selecionar_output_diretorio(self):
        diretorio = QFileDialog.getExistingDirectory(self, "Selecionar Pasta de Saída")
        if diretorio:
            self.output_dir.setText(diretorio)

    def atualizar_lista_arquivos(self, pasta):
        self.list_arquivos.clear()
        for nome_arquivo in os.listdir(pasta):
            if nome_arquivo.lower().endswith('.srt') and os.path.isfile(os.path.join(pasta, nome_arquivo)):
                self.list_arquivos.addItem(nome_arquivo)

    def iniciar_conversao(self):
        pasta_entrada = self.input_dir.text()
        if not pasta_entrada or not os.path.isdir(pasta_entrada):
            self.log_area.append('❌ Erro: Por favor, selecione um diretório de entrada válido.')
            return

        pasta_saida = self.output_dir.text()
        if pasta_saida and not os.path.isdir(pasta_saida):
            os.makedirs(pasta_saida, exist_ok=True)
            self.log_area.append(f'✅ Pasta de saída criada: {pasta_saida}')

        codificacao_destino = self.combo_codificacao.currentText()
        self.log_area.append(f"Iniciando conversão para '{codificacao_destino}'...")

        self.btn_converter.setEnabled(False)
        self.processar_arquivos(pasta_entrada, pasta_saida, codificacao_destino)
        self.btn_converter.setEnabled(True)
        self.log_area.append('\n✅ Processo de conversão concluído!')

    def processar_arquivos(self, pasta_entrada, pasta_saida, codificacao_destino):
        for i in range(self.list_arquivos.count()):
            nome_arquivo = self.list_arquivos.item(i).text()
            caminho_completo_entrada = os.path.join(pasta_entrada, nome_arquivo)
            caminho_completo_saida = os.path.join(pasta_saida if pasta_saida else pasta_entrada, nome_arquivo)

            codificacao_origem = None
            conteudo_texto = None

            try:
                # Ler o arquivo em modo binário para detecção
                with open(caminho_completo_entrada, 'rb') as f_bin:
                    conteudo_bin = f_bin.read()
                    
                    # Tenta a detecção automática com chardet
                    resultado_detec = chardet.detect(conteudo_bin)
                    codificacao_origem = resultado_detec['encoding']

                    # Lista de fallback para tentar caso chardet falhe
                    codificacoes_fallback = ['utf-8', 'cp1252', 'latin1', 'utf-16']

                    # Lógica de tentativa e erro
                    if codificacao_origem and codificacao_origem.lower() != 'ascii':
                        self.log_area.append(f"-> Arquivo '{nome_arquivo}': detectado como '{codificacao_origem}'.")
                        try:
                            conteudo_texto = conteudo_bin.decode(codificacao_origem, errors='replace')
                        except (UnicodeDecodeError, LookupError):
                            # Se a detecção do chardet falhar na prática, tenta o fallback
                            codificacao_origem = None

                    if not codificacao_origem:
                        self.log_area.append(f"-> ⚠️ Aviso: Detecção automática de '{nome_arquivo}' falhou. Tentando codificações comuns...")
                        
                        for fallback_encoding in codificacoes_fallback:
                            try:
                                conteudo_texto = conteudo_bin.decode(fallback_encoding, errors='replace')
                                codificacao_origem = fallback_encoding
                                self.log_area.append(f"   -> Encontrada: '{fallback_encoding}'.")
                                break
                            except (UnicodeDecodeError, LookupError):
                                continue

                    if conteudo_texto is None:
                        self.log_area.append(f"-> ❌ Erro: Não foi possível decodificar '{nome_arquivo}' com as codificações tentadas. Pulando.")
                        continue
                        
                # Recodificar e salvar, ignorando caracteres inválidos
                with open(caminho_completo_saida, 'w', encoding=codificacao_destino, errors='ignore') as f_saida:
                    f_saida.write(conteudo_texto)
                
                self.log_area.append(f"   -> ✅ Conversão para '{codificacao_destino}' concluída com sucesso.\n")

            except Exception as e:
                self.log_area.append(f"-> ❌ Erro inesperado ao processar '{nome_arquivo}': {e}\n")

if __name__ == '__main__':
    app = QApplication([])
    conversor = ConversorLegendas()
    conversor.show()
    app.exec()