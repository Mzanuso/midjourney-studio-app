import sys
import os
import json
import time
import base64
import threading
import websocket
import requests
from datetime import datetime
from PIL import Image
import anthropic
import logging
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                           QHBoxLayout, QPushButton, QLabel, QGridLayout, 
                           QScrollArea, QMessageBox, QFrame, QTextEdit,
                           QSplitter, QListWidget, QFileDialog)
from PyQt5.QtGui import QPixmap, QColor, QPainter
from PyQt5.QtCore import Qt, QSize, pyqtSignal, QThread

# Riutilizziamo RateLimiter da PROMPT.py
class RateLimiter:
    def __init__(self):
        """Inizializza il rate limiter con i limiti globali e per endpoint"""
        self.global_limits = {
            "time_window": 1,  # 1 secondo
            "max_requests": 50  # massimo 50 richieste per secondo
        }
        self.endpoint_limits = {
            "imagine": {"time_window": 1, "max_requests": 5},
            "upscale": {"time_window": 1, "max_requests": 5},
            "variation": {"time_window": 1, "max_requests": 5},
        }
        self.global_requests = []
        self.endpoint_requests = {
            "imagine": [],
            "upscale": [],
            "variation": []
        }
        self.max_retries = 3
        self.base_retry_delay = 1

    # [Resto della classe RateLimiter rimane identico a PROMPT.py]

class StatusIndicator(QWidget):
    def __init__(self, label, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 0, 5, 0)
        
        self.text_label = QLabel(label)
        layout.addWidget(self.text_label)
        
        self.light = QWidget()
        self.light.setFixedSize(12, 12)
        self.is_connected = False
        layout.addWidget(self.light)

    # [Resto della classe StatusIndicator rimane identico a PROMPT.py]

class DiscordClient:
    def __init__(self, token, app_reference):
        self.token = token
        self.app = app_reference
        self.ws = None
        self.session_id = None
        self.heartbeat_interval = None
        self.last_sequence = None
        self.rate_limiter = RateLimiter()
        self.message_tracker = MessageTracker()

        # Headers configurazione
        self.headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache"
        }
        self.base_url = "https://discord.com/api/v9"
        
        # Handlers per diversi tipi di eventi
        self.event_handlers = {
            "MESSAGE_CREATE": self.handle_message_create,
            "MESSAGE_UPDATE": self.handle_message_update,
            "INTERACTION_CREATE": self.handle_interaction
        }

    def connect(self):
        """Stabilisce la connessione WebSocket con Discord"""
        try:
            response = requests.get(
                f"{self.base_url}/gateway",
                headers=self.headers,
                timeout=10
            )
            if response.status_code != 200:
                self.app.log_message(f"[ERROR] Failed to get gateway: {response.status_code}")
                return False

            gateway_url = response.json()["url"]
            websocket.enableTrace(True)
            self.ws = websocket.WebSocketApp(
                f"{gateway_url}/?v=9&encoding=json",
                on_message=self.on_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open,
                header=[f"Authorization: {self.token}"]
            )
            
            return True
        except Exception as e:
            self.app.log_message(f"[ERROR] Connection setup failed: {str(e)}")
            return False

    def start(self):
        """Avvia il client Discord"""
        if self.connect():
            self.ws.run_forever()

    def get_latest_command_version(self):
        """Ottiene l'ultima versione dei comandi Midjourney"""
        try:
            response = requests.get(
                f"{self.base_url}/applications/936929561302675456/commands",
                headers=self.headers
            )
            
            if response.status_code == 200:
                for command in response.json():
                    if command["name"] == "imagine":
                        return command["version"]
            
            self.app.log_message("[ERROR] Failed to fetch command version")
            return None
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to get command version: {str(e)}")
            return None

    def send_imagine_command(self, channel_id, guild_id, prompt):
        """Invia il comando imagine a Midjourney"""
        if not self.session_id:
            self.app.log_message("[ERROR] No session ID available")
            return False

        try:
            version = self.get_latest_command_version()
            if not version:
                return False

            payload = {
                "type": 2,
                "application_id": "936929561302675456",
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
                "session_id": self.session_id,
                "data": {
                    "version": version,
                    "id": "938956540159881230",
                    "name": "imagine",
                    "type": 1,
                    "options": [{"type": 3, "name": "prompt", "value": prompt}],
                    "attachments": []
                }
            }

            response = requests.post(
                f"{self.base_url}/interactions",
                headers=self.headers,
                json=payload
            )

            if response.status_code == 204:
                self.app.log_message("[INFO] Imagine command sent successfully")
                return True

            self.app.log_message(f"[ERROR] Failed to send imagine command: {response.status_code}")
            return False

        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to send imagine command: {str(e)}")
            return False

    def send_upscale_command(self, channel_id, guild_id, message_id, index, button_custom_id):
        """Invia il comando di upscale"""
        if not self.session_id:
            self.app.log_message("[ERROR] No session ID available")
            return False

        try:
            payload = {
                "type": 3,
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "application_id": "936929561302675456",
                "session_id": self.session_id,
                "data": {
                    "component_type": 2,
                    "custom_id": button_custom_id
                }
            }

            response = requests.post(
                f"{self.base_url}/interactions",
                headers=self.headers,
                json=payload
            )

            if response.status_code == 204:
                self.app.log_message(f"[INFO] Upscale {index} command sent successfully")
                return True

            self.app.log_message(f"[ERROR] Failed to send upscale command: {response.status_code}")
            return False

        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to send upscale command: {str(e)}")
            return False

    def send_variation_command(self, channel_id, guild_id, message_id, index):
        """Invia il comando per generare una variazione"""
        if not self.session_id:
            self.app.log_message("[ERROR] No session ID available")
            return False

        try:
            button_custom_id = f"MJ::JOB::variation::{index}"
            
            payload = {
                "type": 3,
                "guild_id": str(guild_id),
                "channel_id": str(channel_id),
                "message_id": str(message_id),
                "application_id": "936929561302675456",
                "session_id": self.session_id,
                "data": {
                    "component_type": 2,
                    "custom_id": button_custom_id
                }
            }

            response = requests.post(
                f"{self.base_url}/interactions",
                headers=self.headers,
                json=payload
            )

            if response.status_code == 204:
                self.app.log_message(f"[INFO] Variation {index} command sent successfully")
                return True

            self.app.log_message(f"[ERROR] Failed to send variation command: {response.status_code}")
            return False

        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to send variation command: {str(e)}")
            return False

    def handle_midjourney_message(self, message_data):
        """Gestisce i messaggi da Midjourney"""
        try:
            if "attachments" not in message_data or not message_data["attachments"]:
                return

            message_id = message_data.get("id")
            buttons_data = {}

            # Estrae custom_id dei bottoni
            if "components" in message_data:
                for row in message_data.get("components", []):
                    for component in row.get("components", []):
                        if "custom_id" in component and component["custom_id"].startswith("MJ::JOB::"):
                            action_type = "upscale" if "upsample" in component["custom_id"] else "variation"
                            index = component["custom_id"].split("::")[-2]
                            buttons_data[f"{action_type}_{index}"] = component["custom_id"]

            for attachment in message_data["attachments"]:
                if not any(attachment["filename"].lower().endswith(ext) 
                          for ext in ['.png', '.jpg', '.jpeg']):
                    continue

                response = requests.get(attachment["url"])
                if response.status_code == 200:
                    # Determina il percorso di salvataggio
                    save_path = self.determine_save_path(message_data)
                    
                    with open(save_path, 'wb') as f:
                        f.write(response.content)

                    # Emetti il segnale per la nuova immagine
                    self.app.newImageReceived.emit(
                        save_path,
                        self.extract_sref(message_data.get("content", "")),
                        self.determine_category(message_data.get("content", "")),
                        None,  # subcategory
                        message_id,
                        buttons_data
                    )

        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to handle Midjourney message: {str(e)}")

    def on_message(self, ws, message):
        """Gestisce i messaggi WebSocket"""
        try:
            data = json.loads(message)
            
            if data["op"] == 10:  # Hello
                self.heartbeat_interval = data["d"]["heartbeat_interval"]
                threading.Thread(target=self.heartbeat, daemon=True).start()
                self.send_identify()
                
            elif data["op"] == 0:  # Dispatch
                self.last_sequence = data["s"]
                
                if data["t"] == "READY":
                    self.session_id = data["d"]["session_id"]
                    self.app.discord_status.set_status(True)
                    self.app.log_message("[INFO] Discord client ready")
                    
                elif data["t"] in self.event_handlers:
                    self.event_handlers[data["t"]](data["d"])
                    
        except Exception as e:
            self.app.log_message(f"[ERROR] WebSocket message processing failed: {str(e)}")

    def on_error(self, ws, error):
        """Gestisce gli errori WebSocket"""
        self.app.log_message(f"[ERROR] WebSocket error: {str(error)}")
        self.app.discord_status.set_status(False)

    def on_close(self, ws, close_status_code, close_msg):
        """Gestisce la chiusura della connessione WebSocket"""
        self.app.log_message(f"[INFO] WebSocket closed: {close_msg}")
        self.app.discord_status.set_status(False)

    def on_open(self, ws):
        """Gestisce l'apertura della connessione WebSocket"""
        self.app.log_message("[INFO] WebSocket connection opened")
        self.app.discord_status.set_status(True)

    def send_identify(self):
        """Invia il payload di identificazione"""
        try:
            identify_payload = {
                "op": 2,
                "d": {
                    "token": self.token,
                    "properties": {
                        "$os": "windows",
                        "$browser": "chrome",
                        "$device": "pc"
                    },
                    "presence": {
                        "status": "online",
                        "since": 0,
                        "activities": [],
                        "afk": False
                    }
                }
            }
            self.ws.send(json.dumps(identify_payload))
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to send identify payload: {str(e)}")

    def heartbeat(self):
        """Mantiene viva la connessione WebSocket"""
        while self.ws and self.ws.sock and self.ws.sock.connected:
            if self.heartbeat_interval:
                try:
                    payload = {"op": 1, "d": self.last_sequence}
                    self.ws.send(json.dumps(payload))
                    time.sleep(self.heartbeat_interval / 1000)
                except Exception as e:
                    self.app.log_message(f"[ERROR] Heartbeat failed: {str(e)}")
                    break
    def extract_sref(self, content):
        """Estrae il sref dal contenuto del messaggio"""
        try:
            sref_match = re.search(r'--sref\s+(\d+)', content)
            return sref_match.group(1) if sref_match else None
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to extract sref: {str(e)}")
            return None

    def determine_category(self, content):
        """Determina la categoria dal contenuto"""
        try:
            # Mappa delle categorie comuni
            categories = {
                'Product_Photography': ['product', 'commercial'],
                'Still_Life': ['still life', 'arrangement'],
                'Interior_Photography': ['interior', 'room'],
                'Landscape': ['landscape', 'scenic'],
                'Architecture': ['building', 'architecture'],
                'Fine_Art': ['fine art', 'artistic']
            }
            
            content_lower = content.lower()
            for category, keywords in categories.items():
                if any(keyword in content_lower for keyword in keywords):
                    return category
                    
            return None
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to determine category: {str(e)}")
            return None

    def determine_save_path(self, message_data):
        """Determina il percorso di salvataggio per una nuova immagine"""
        try:
            content = message_data.get("content", "")
            sref = self.extract_sref(content)
            
            if sref:
                # Percorso per immagini con sref
                base_path = os.path.join(self.app.analysis_dir, f"sref_{sref}")
                os.makedirs(base_path, exist_ok=True)
                
                existing = glob.glob(os.path.join(base_path, f"sref_{sref}_*.png"))
                number = len(existing) + 1
                
                return os.path.join(base_path, f"sref_{sref}_{number:03d}.png")
            else:
                # Percorso per immagini base
                base_path = os.path.join(self.app.output_dir, "00_BASE")
                os.makedirs(base_path, exist_ok=True)
                
                existing = glob.glob(os.path.join(base_path, "img_*.png"))
                number = len(existing) + 1
                
                return os.path.join(base_path, f"img_{number:03d}.png")
                
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to determine save path: {str(e)}")
            # Percorso di fallback
            return os.path.join(self.app.output_dir, f"error_{int(time.time())}.png")
        
class ClaudeAnalyzer:
    def __init__(self, api_key, app_reference):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.app = app_reference
        self.base_output_dir = os.path.join(self.app.base_dir, "midjourney_output")
        self.analysis_queue = []
        self.processing = False

    def analyze_image(self, image_path):
        try:
            if not os.path.exists(image_path):
                self.app.log_message(f"[ERROR] Image file not found: {image_path}")
                return None

            # Codifica l'immagine in base64
            with open(image_path, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')

            # Preparazione del prompt per Claude
            analysis_prompt = """[Prompt da PROMPT.py]"""  # Inserire il prompt completo

            try:
                response = self.client.messages.create(
                    model="claude-3-sonnet-20240229",
                    max_tokens=1500,
                    temperature=0.7,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": analysis_prompt
                                },
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": base64_image
                                    }
                                }
                            ]
                        }
                    ]
                )

                # Processa e salva l'analisi
                analysis_result = self.process_claude_response(response, image_path)
                return analysis_result

            except Exception as e:
                self.app.log_message(f"[ERROR] Claude analysis failed: {str(e)}")
                return None

        except Exception as e:
            self.app.log_message(f"[ERROR] Analysis failed: {str(e)}")
            return None

    def process_claude_response(self, response, image_path):
        # Implementazione del processing della risposta
        pass

class ImageManager:
    def __init__(self, app_reference):
        self.app = app_reference
        self.image_tracking = {
            "series": {},      # Per set di immagini correlate
            "categories": {},  # Organizzazione per categoria
            "upscales": {},   # Tracking upscale
            "variations": {}   # Tracking variazioni
        }
        self.selected_images = set()
        self.load_tracking_state()

    def load_tracking_state(self):
        try:
            state_file = os.path.join(self.app.system_dir, "tracking_state.json")
            if os.path.exists(state_file):
                with open(state_file, 'r', encoding='utf-8') as f:
                    state = json.load(f)
                    self.image_tracking.update(state)
                self.app.log_message("[INFO] Tracking state loaded")
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to load tracking state: {str(e)}")

    def save_tracking_state(self):
        try:
            state_file = os.path.join(self.app.system_dir, "tracking_state.json")
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(self.image_tracking, f, indent=2)
            self.app.log_message("[INFO] Tracking state saved")
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to save tracking state: {str(e)}")

class ImageThumbnail(QWidget):
    clicked = pyqtSignal(str)
    selectionChanged = pyqtSignal(str, bool)

    def __init__(self, image_path, parent=None):
        super().__init__(parent)
        self.image_path = image_path
        
        # Layout principale
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Container per checkbox e immagine
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(5, 5, 5, 5)
        
        # Checkbox con sfondo semi-trasparente
        checkbox_container = QWidget()
        checkbox_layout = QHBoxLayout(checkbox_container)
        checkbox_layout.addStretch()
        
        self.number_label = QLabel("")
        self.number_label.setStyleSheet("""
            QLabel {
                color: white;
                background-color: #0066cc;
                border-radius: 10px;
                padding: 2px 6px;
            }
        """)
        self.number_label.hide()
        checkbox_layout.addWidget(self.number_label)
        
        self.checkbox = QCheckBox()
        self.checkbox.setStyleSheet("""
            QCheckBox {
                background-color: rgba(255, 255, 255, 0.8);
                border-radius: 3px;
                padding: 2px;
            }
        """)
        self.checkbox.stateChanged.connect(self.on_checkbox_changed)
        checkbox_layout.addWidget(self.checkbox)
        
        container_layout.addWidget(checkbox_container)
        
        # Thumbnail
        self.thumbnail_label = QLabel()
        pixmap = QPixmap(image_path)
        scaled_pixmap = pixmap.scaled(
            180, 180, 
            Qt.KeepAspectRatio, 
            Qt.SmoothTransformation
        )
        self.thumbnail_label.setPixmap(scaled_pixmap)
        container_layout.addWidget(self.thumbnail_label)
        
        # Nome file
        filename = os.path.basename(image_path)
        file_label = QLabel(filename)
        file_label.setWordWrap(True)
        container_layout.addWidget(file_label)
        
        layout.addWidget(container)
        
        # Stile e interattività
        self.setMouseTracking(True)
        self.setStyleSheet("""
            QWidget:hover { 
                background-color: #f0f0f0; 
                border-radius: 5px;
            }
        """)

    def set_selection_number(self, number):
        if number is not None:
            self.number_label.setText(str(number))
            self.number_label.show()
        else:
            self.number_label.hide()

    def on_checkbox_changed(self, state):
        self.selectionChanged.emit(self.image_path, state == Qt.Checked)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.checkbox.geometry().contains(event.pos()):
                self.clicked.emit(self.image_path)

class ImageGallery(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent_app = parent
        self.setWidgetResizable(True)
        
        # Widget contenitore
        self.container = QWidget()
        self.layout = QGridLayout(self.container)
        self.layout.setSpacing(10)
        self.setWidget(self.container)
        
        # Tracciamento immagini
        self.thumbnails = {}
        self.current_folder = None
        
        # Bottoni azione
        self.setup_action_buttons()

    def setup_action_buttons(self):
        button_container = QWidget()
        button_layout = QHBoxLayout(button_container)
        
        # Upscale buttons
        for i in range(1, 5):
            btn = QPushButton(f"U{i}")
            btn.setEnabled(False)
            btn.clicked.connect(lambda x, idx=i: self.handle_upscale(idx))
            button_layout.addWidget(btn)
        
        # Variation buttons (nuovo)
        for i in range(1, 5):
            btn = QPushButton(f"V{i}")
            btn.setEnabled(False)
            btn.clicked.connect(lambda x, idx=i: self.handle_variation(idx))
            button_layout.addWidget(btn)
        
        self.layout.addWidget(button_container, 0, 0, 1, -1)

    def load_folder(self, folder_path):
        # Pulisci layout esistente
        self.clear_gallery()
        
        # Carica nuove immagini
        image_files = [f for f in os.listdir(folder_path) 
                      if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
        
        row = 1  # Prima riga per i bottoni
        col = 0
        max_cols = 4
        
        for img_file in image_files:
            img_path = os.path.join(folder_path, img_file)
            thumbnail = ImageThumbnail(img_path)
            thumbnail.clicked.connect(self.open_editor)
            thumbnail.selectionChanged.connect(self.handle_selection)
            
            self.layout.addWidget(thumbnail, row, col)
            self.thumbnails[img_path] = thumbnail
            
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

    def clear_gallery(self):
        while self.layout.count() > 1:  # Mantieni i bottoni
            item = self.layout.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        self.thumbnails.clear()

    def handle_selection(self, image_path, is_selected):
        if is_selected:
            self.parent_app.image_manager.selected_images.add(image_path)
        else:
            self.parent_app.image_manager.selected_images.discard(image_path)
        
        self.update_button_states()

    def update_button_states(self):
        selected_count = len(self.parent_app.image_manager.selected_images)
        enable_buttons = selected_count == 1
        
        # Aggiorna stato bottoni
        for btn in self.findChildren(QPushButton):
            if btn.text().startswith(('U', 'V')):
                btn.setEnabled(enable_buttons)

    def handle_upscale(self, index):
        if len(self.parent_app.image_manager.selected_images) != 1:
            return
            
        image_path = next(iter(self.parent_app.image_manager.selected_images))
        message_id = self.parent_app.image_manager.image_tracking.get(image_path, {}).get('message_id')
        
        if message_id:
            self.parent_app.discord_client.send_upscale_command(
                self.parent_app.config["CHANNEL_ID"],
                self.parent_app.config["GUILD_ID"],
                message_id,
                index
            )

    def handle_variation(self, index):
        if len(self.parent_app.image_manager.selected_images) != 1:
            return
            
        image_path = next(iter(self.parent_app.image_manager.selected_images))
        message_id = self.parent_app.image_manager.image_tracking.get(image_path, {}).get('message_id')
        
        if message_id:
            self.parent_app.discord_client.send_variation_command(
                self.parent_app.config["CHANNEL_ID"],
                self.parent_app.config["GUILD_ID"],
                message_id,
                index
            )

    def open_editor(self, image_path):
        try:
            folder_path = os.path.dirname(image_path)
            editor = ImageEditor(image_path, folder_path)
            editor.exec_()
        except Exception as e:
            self.parent_app.log_message(f"[ERROR] Failed to open editor: {str(e)}")

class RatingSystem:
    def __init__(self, app_reference):
        self.app = app_reference
        self.ratings_file = os.path.join(app_reference.system_dir, "folder_ratings.json")
        self.ratings = self.load_ratings()

    def load_ratings(self):
        try:
            if os.path.exists(self.ratings_file):
                with open(self.ratings_file, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to load ratings: {str(e)}")
            return {}

    def save_ratings(self):
        try:
            with open(self.ratings_file, 'w') as f:
                json.dump(self.ratings, f, indent=2)
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to save ratings: {str(e)}")

    def set_rating(self, folder_name, rating):
        self.ratings[folder_name] = rating
        self.save_ratings()

    def get_rating(self, folder_name):
        return self.ratings.get(folder_name, 0)

class StarRating(QWidget):
    ratingChanged = pyqtSignal(int)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.init_ui()

    def init_ui(self):
        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(5, 5, 5, 5)
        self.buttons = []
        self.current_rating = 0
        
        # Crea i bottoni stella
        for i in range(5):
            btn = QPushButton('★')
            btn.setFixedSize(25, 25)
            btn.setStyleSheet("""
                QPushButton {
                    color: #ccc;
                    border: none;
                    font-size: 16px;
                    background: transparent;
                }
                QPushButton:hover {
                    color: #ffd700;
                }
            """)
            btn.clicked.connect(lambda checked, x=i+1: self.set_rating(x))
            self.buttons.append(btn)
            self.layout.addWidget(btn)

    def set_rating(self, rating):
        self.current_rating = rating
        self.update_stars()
        self.ratingChanged.emit(rating)

    def update_stars(self):
        for i, btn in enumerate(self.buttons):
            if i < self.current_rating:
                btn.setStyleSheet("""
                    QPushButton {
                        color: #ffd700;
                        border: none;
                        font-size: 16px;
                        background: transparent;
                    }
                    QPushButton:hover {
                        color: #ffd700;
                    }
                """)
            else:
                btn.setStyleSheet("""
                    QPushButton {
                        color: #ccc;
                        border: none;
                        font-size: 16px;
                        background: transparent;
                    }
                    QPushButton:hover {
                        color: #ffd700;
                    }
                """)

class ClaudeAnalysisManager:
    def __init__(self, app_reference):
        self.app = app_reference
        self.analysis_queue = []
        self.is_processing = False
        self.current_analysis = None
        self.prompt_template = """
You are an expert in interpreting complex images.
Your main role is to give a real subject and surrounding environment to the figures present in the analyzed image.
The new subject must be consistent with the identified category/subcategory.
The main subjects of the two prompts are not abstract shapes.

[ANALISI DETTAGLIATA RICHIESTA]:

1. PATTERN AND FORM ANALYSIS:
- Dominant shapes and lines
- Texture patterns
- Color relationships and harmonies
- Motion and flow directions
- Depth and dimensionality
- Compositional balance
- Light/shadow interactions

2. CREATIVE INTERPRETATION:
- Concrete subjects/scenes that emerge
- Mood and emotional qualities
- Symbolic elements
- Psychological aspects

3. COLOR ANALYSIS:
[5 predominant colors in RGB format]
- Role in composition
- Emotional impact per color

4. TECHNICAL SPECIFICATIONS:
- Camera perspective
- Lighting setup
- Key technical elements

5. GENERATED PROMPTS:
Create TWO distinct prompts:
1. Photography-focused prompt
2. Creative/artistic variation

Each prompt must include:
- Clear subject description
- Technical specifications
- Style elements
- Emotional qualities

Response Format:
---
PATTERN ANALYSIS:
[Detailed analysis]

CREATIVE INTERPRETATION:
[Your interpretation]

COLOR ANALYSIS:
[5 colors with roles]

TECHNICAL NOTES:
[Key specifications]

PROMPT 1:
[Photography prompt]

PROMPT 2:
[Creative variation]
---"""

    async def analyze_image(self, image_path):
        """Analizza un'immagine usando Claude"""
        try:
            # Converti immagine in base64
            with open(image_path, "rb") as img_file:
                image_data = base64.b64encode(img_file.read()).decode('utf-8')

            response = await self.app.claude_client.messages.create(
                model="claude-3-sonnet-20240229",
                max_tokens=2000,
                temperature=0.7,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": self.prompt_template
                            },
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": image_data
                                }
                            }
                        ]
                    }
                ]
            )

            analysis_result = self.parse_response(response)
            self.save_analysis(image_path, analysis_result)
            return analysis_result

        except Exception as e:
            self.app.log_message(f"[ERROR] Analysis failed: {str(e)}")
            return None

    def parse_response(self, response):
        """Analizza la risposta di Claude e la struttura"""
        try:
            text = response.content[0].text
            sections = [
                "PATTERN ANALYSIS",
                "CREATIVE INTERPRETATION",
                "COLOR ANALYSIS",
                "TECHNICAL NOTES",
                "PROMPT 1",
                "PROMPT 2"
            ]
            
            result = {}
            current_section = None
            current_content = []
            
            for line in text.split('\n'):
                line = line.strip()
                if not line:
                    continue
                    
                is_section = False
                for section in sections:
                    if line.startswith(section):
                        if current_section:
                            result[current_section] = '\n'.join(current_content).strip()
                            current_content = []
                        current_section = section.lower().replace(' ', '_')
                        is_section = True
                        break
                        
                if not is_section and current_section:
                    current_content.append(line)
            
            if current_section and current_content:
                result[current_section] = '\n'.join(current_content).strip()
                
            return result

        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to parse Claude response: {str(e)}")
            return None

    def save_analysis(self, image_path, analysis):
        """Salva l'analisi in un file di testo"""
        try:
            analysis_path = f"{os.path.splitext(image_path)[0]}_analysis.txt"
            with open(analysis_path, 'w', encoding='utf-8') as f:
                for section, content in analysis.items():
                    f.write(f"{section.upper()}\n")
                    f.write("="* len(section) + "\n\n")
                    f.write(content + "\n\n")
                    
            self.app.log_message(f"[INFO] Analysis saved to: {analysis_path}")
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to save analysis: {str(e)}")

class MidjourneyStudioApp(QMainWindow):
    newImageReceived = pyqtSignal(str, str, str, str, str, dict)
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Midjourney Studio")
        self.setGeometry(100, 100, 1400, 800)

        # Inizializzazione base paths
        self.base_dir = "D:\\AI_Art_Studio"
        self.output_dir = os.path.join(self.base_dir, "midjourney_output")
        self.analysis_dir = os.path.join(self.output_dir, "01_ANALYSIS")
        self.cards_dir = os.path.join(self.output_dir, "CARD")
        self.system_dir = os.path.join(self.base_dir, "system")
        self.log_dir = os.path.join(self.system_dir, "logs")
        self.file_manager = FileManager(self)

        # Creazione directories
        self.setup_directories()
        
        # Setup logging
        self.setup_logging()
        
        # Inizializza sistema di rating
        self.rating_system = RatingSystem(self)
        
        # Inizializza image manager
        self.image_manager = ImageManager(self)
        
        # Setup UI
        self.init_ui()
        
        # Setup clients
        self.setup_clients()
        
        # Connetti segnali
        self.newImageReceived.connect(self.handle_new_image)
        
        # Carica cartelle iniziali
        self.load_initial_folders()

        # Timer per aggiornamento interfaccia
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_interface_states)
        self.update_timer.start(1000)  # Aggiorna ogni secondo

    def init_ui(self):
        """Inizializza l'interfaccia utente"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        
        # Splitter principale
        main_splitter = QSplitter(Qt.Horizontal)
        
        # Panel sinistro (browser e controlli)
        left_panel = QWidget()
        left_panel.setObjectName("left_panel")  # Importante per i riferimenti
        left_layout = QVBoxLayout(left_panel)
        
        # Status e progress bar layout
        self.status_layout = QHBoxLayout()
        self.discord_status = StatusIndicator("Discord")
        self.claude_status = StatusIndicator("Claude")
        self.status_layout.addWidget(self.discord_status)
        self.status_layout.addWidget(self.claude_status)
        self.status_layout.addStretch()
        left_layout.addLayout(self.status_layout)
        
        # Directory selector con autocompletamento
        dir_layout = QHBoxLayout()
        self.dir_input = QLineEdit()
        self.dir_input.setText(self.output_dir)
        dir_layout.addWidget(self.dir_input)
        
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self.browse_directory)
        dir_layout.addWidget(browse_btn)
        left_layout.addLayout(dir_layout)
        
        # Controlli ordinamento
        sort_layout = QHBoxLayout()
        self.sort_by_files = QPushButton("Sort by Files")
        self.sort_by_rating = QPushButton("Sort by Rating")
        self.sort_by_files.clicked.connect(lambda: self.refresh_folder_list("files"))
        self.sort_by_rating.clicked.connect(lambda: self.refresh_folder_list("rating"))
        sort_layout.addWidget(self.sort_by_files)
        sort_layout.addWidget(self.sort_by_rating)
        left_layout.addLayout(sort_layout)
        
        # Lista cartelle
        self.folder_list = QListWidget()
        self.folder_list.itemClicked.connect(self.folder_selected)
        left_layout.addWidget(self.folder_list)
        
        # Bottoni azione
        action_layout = QHBoxLayout()
        
        self.prompt_btn = QPushButton("Prompt")
        self.prompt_btn.clicked.connect(self.open_prompt_app)
        self.prompt_btn.setEnabled(False)
        
        self.card_btn = QPushButton("Create Card")
        self.card_btn.clicked.connect(self.open_card_editor)
        self.card_btn.setEnabled(False)
        
        self.analyze_btn = QPushButton("Analyze Selected")
        self.analyze_btn.clicked.connect(self.analyze_selected_images)
        self.analyze_btn.setEnabled(False)
        
        action_layout.addWidget(self.prompt_btn)
        action_layout.addWidget(self.card_btn)
        action_layout.addWidget(self.analyze_btn)
        left_layout.addLayout(action_layout)
        
        main_splitter.addWidget(left_panel)
        
        # Galleria centrale
        gallery_container = QWidget()
        gallery_layout = QVBoxLayout(gallery_container)
        
        # Toolbar della galleria
        gallery_toolbar = QHBoxLayout()
        
        # Upscale buttons
        for i in range(1, 5):
            btn = QPushButton(f"U{i}")
            btn.setEnabled(False)
            btn.clicked.connect(lambda x, idx=i: self.handle_upscale(idx))
            gallery_toolbar.addWidget(btn)
            
        # Variation buttons
        for i in range(1, 5):
            btn = QPushButton(f"V{i}")
            btn.setEnabled(False)
            btn.clicked.connect(lambda x, idx=i: self.handle_variation(idx))
            gallery_toolbar.addWidget(btn)
            
        gallery_layout.addLayout(gallery_toolbar)
        
        # Galleria immagini
        self.gallery = ImageGallery(self)
        gallery_layout.addWidget(self.gallery)
        
        main_splitter.addWidget(gallery_container)
        
        # Panel destro (log e analisi)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        
        # Area analisi
        self.analysis_text = QTextEdit()
        self.analysis_text.setReadOnly(True)
        right_layout.addWidget(self.analysis_text)
        
        # Log area
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        right_layout.addWidget(self.log_text)
        
        main_splitter.addWidget(right_panel)
        
        # Aggiunge splitter al layout principale
        main_layout.addWidget(main_splitter)
        
        # Imposta dimensioni relative dei pannelli
        main_splitter.setSizes([300, 800, 300])
        
        # Aggiorna stati interfaccia iniziali
        self.update_interface_states()

    def browse_directory(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Directory", self.output_dir)
        if directory:
            self.dir_input.setText(directory)
            self.load_folders(directory)

    def load_initial_folders(self):
        """Carica le cartelle iniziali"""
        initial_path = self.dir_input.text()
        if os.path.exists(initial_path):
            self.load_folders(initial_path)

    def load_folders(self, directory):
        """Carica le cartelle nel list widget"""
        try:
            self.folder_list.clear()
            folders = []
            
            for folder in os.listdir(directory):
                folder_path = os.path.join(directory, folder)
                if os.path.isdir(folder_path):
                    # Conta file nella cartella
                    num_files = len([f for f in os.listdir(folder_path) 
                                   if f.lower().endswith(('.jpg', '.png', '.txt'))])
                    
                    # Ottieni rating se presente
                    rating = self.image_manager.image_tracking.get("ratings", {}).get(folder, 0)
                    
                    folders.append({
                        'name': folder,
                        'num_files': num_files,
                        'rating': rating
                    })
            
            # Ordina in base al criterio corrente
            folders.sort(key=lambda x: x['num_files'], reverse=True)
            
            # Popola la lista
            for folder in folders:
                item_text = f"{folder['name']} ({folder['num_files']} files, {folder['rating']}★)"
                self.folder_list.addItem(item_text)
                
        except Exception as e:
            self.log_message(f"[ERROR] Failed to load folders: {str(e)}")

    def folder_selected(self, item):
        """Gestisce la selezione di una cartella"""
        try:
            folder_name = item.text().split(" (")[0]
            folder_path = os.path.join(self.dir_input.text(), folder_name)
            
            # Aggiorna path corrente
            self.current_folder = folder_path
            
            # Carica immagini nella galleria
            self.gallery.load_folder(folder_path)
            
            # Abilita bottoni
            self.prompt_btn.setEnabled(True)
            self.card_btn.setEnabled(True)
            
        except Exception as e:
            self.log_message(f"[ERROR] Failed to load folder: {str(e)}")

    def handle_new_image(self, image_path, sref, category, subcategory, message_id, buttons_data):
        """Gestisce l'arrivo di una nuova immagine"""
        try:
            # Aggiorna tracking
            self.image_manager.image_tracking["series"][sref] = self.image_manager.image_tracking["series"].get(sref, [])
            self.image_manager.image_tracking["series"][sref].append({
                'path': image_path,
                'message_id': message_id,
                'buttons_data': buttons_data,
                'category': category,
                'subcategory': subcategory
            })
            
            # Salva stato tracking
            self.image_manager.save_tracking_state()
            
            # Se la cartella corrente è quella dell'immagine, aggiorna la galleria
            if self.current_folder and os.path.dirname(image_path) == self.current_folder:
                self.gallery.load_folder(self.current_folder)
                
        except Exception as e:
            self.log_message(f"[ERROR] Failed to handle new image: {str(e)}")

    def open_prompt_app(self):
        """Apre l'applicazione Prompt"""
        if hasattr(self, 'current_folder'):
            try:
                # Costruisci il percorso completo
                prompt_path = os.path.join(self.base_dir, "midjourney_output", "APP", "PROMPT.py")
                
                if not os.path.exists(prompt_path):
                    raise FileNotFoundError(f"PROMPT.py not found in: {prompt_path}")
                
                # Avvia PROMPT.py con il percorso della cartella
                process = subprocess.Popen(
                    [sys.executable, prompt_path, self.current_folder],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                
            except Exception as e:
                self.log_message(f"[ERROR] Failed to open Prompt app: {str(e)}")
                QMessageBox.warning(self, "Error", str(e))
        else:
            QMessageBox.warning(self, "Warning", "Please select a folder first")

    def open_card_editor(self):
        """Apre l'editor delle card"""
        if hasattr(self, 'current_folder'):
            try:
                card_path = os.path.join(self.base_dir, "midjourney_output", "APP", "CARD.py")
                
                if not os.path.exists(card_path):
                    raise FileNotFoundError(f"CARD.py not found in: {card_path}")
                
                # Avvia CARD.py
                process = subprocess.Popen(
                    [sys.executable, card_path, self.current_folder],
                    creationflags=subprocess.CREATE_NEW_CONSOLE
                )
                
            except Exception as e:
                self.log_message(f"[ERROR] Failed to open Card editor: {str(e)}")
                QMessageBox.warning(self, "Error", str(e))
        else:
            QMessageBox.warning(self, "Warning", "Please select a folder first")

    def init_rating_system(self):
        """Inizializza il sistema di rating"""
        self.rating_system = RatingSystem(self)
        
        # Aggiungi star rating widget al left panel
        self.star_rating = StarRating()
        self.star_rating.ratingChanged.connect(self.update_folder_rating)
        
        # Trova il layout del pannello sinistro
        left_panel = self.findChild(QWidget, "left_panel")
        if left_panel:
            left_layout = left_panel.layout()
            left_layout.insertWidget(left_layout.count() - 1, self.star_rating)

    def update_folder_rating(self, rating):
        """Aggiorna il rating della cartella selezionata"""
        if hasattr(self, 'current_folder'):
            self.rating_system = RatingSystem(self)
            folder_name = os.path.basename(self.current_folder)
            self.rating_system.set_rating(folder_name, rating)
            self.refresh_folder_list()

    def refresh_folder_list(self, sort_by="files"):
        """Aggiorna la lista delle cartelle con ordinamento"""
        directory = self.dir_input.text()
        if not directory:
            return
            
        try:
            folders = []
            for folder in os.listdir(directory):
                folder_path = os.path.join(directory, folder)
                if os.path.isdir(folder_path):
                    num_files = len([f for f in os.listdir(folder_path) 
                                   if f.lower().endswith(('.jpg', '.png', '.txt'))])
                    rating = self.rating_system.get_rating(folder)
                    folders.append({
                        'name': folder,
                        'num_files': num_files,
                        'rating': rating
                    })
            
            # Ordina in base al criterio selezionato
            if sort_by == "files":
                folders.sort(key=lambda x: x['num_files'], reverse=True)
            else:  # sort_by == "rating"
                folders.sort(key=lambda x: (x['rating'], x['num_files']), reverse=True)
            
            # Aggiorna la lista
            self.folder_list.clear()
            for folder in folders:
                stars = "★" * folder['rating']
                item_text = f"{folder['name']} ({folder['num_files']} files) {stars}"
                self.folder_list.addItem(item_text)
                
        except Exception as e:
            self.log_message(f"[ERROR] Failed to refresh folder list: {str(e)}")

    def analyze_selected_images(self):
        """Analizza le immagini selezionate"""
        if not hasattr(self, 'claude_manager'):
            self.claude_manager = ClaudeAnalysisManager(self)
            
        selected_images = self.image_manager.selected_images
        if not selected_images:
            QMessageBox.warning(self, "Warning", "No images selected for analysis")
            return
            
        try:
            for image_path in selected_images:
                asyncio.run(self.claude_manager.analyze_image(image_path))
                
            # Aggiorna la visualizzazione
            if hasattr(self, 'current_folder'):
                self.gallery.load_folder(self.current_folder)
                
            QMessageBox.information(self, "Success", "Analysis completed")
            
        except Exception as e:
            self.log_message(f"[ERROR] Analysis failed: {str(e)}")
            QMessageBox.warning(self, "Error", str(e))

    def update_interface_states(self):
        """Aggiorna lo stato dell'interfaccia in base alle selezioni"""
        selected_count = len(self.image_manager.selected_images)
        
        # Abilita/disabilita bottoni upscale e variation
        for btn in self.findChildren(QPushButton):
            if any(prefix in btn.text() for prefix in ['U', 'V']):
                btn.setEnabled(selected_count == 1)
        
        # Abilita/disabilita altri controlli
        self.analyze_btn.setEnabled(selected_count > 0)
        self.prompt_btn.setEnabled(hasattr(self, 'current_folder'))
        self.card_btn.setEnabled(selected_count > 0 and selected_count <= 5)

    def show_generation_progress(self, show=True, message=None):
        """Mostra/nasconde indicatore di progresso generazione"""
        if not hasattr(self, 'progress_label'):
            self.progress_label = QLabel()
            self.progress_label.setStyleSheet("""
                QLabel {
                    background-color: #2ecc71;
                    color: white;
                    padding: 5px;
                    border-radius: 3px;
                }
            """)
            self.status_layout.addWidget(self.progress_label)
            self.progress_label.hide()

        if show:
            self.progress_label.setText(message or "Generating...")
            self.progress_label.show()
        else:
            self.progress_label.hide()

    def show_notification(self, message, level="info"):
        """Mostra una notifica all'utente"""
        colors = {
            "info": "#2ecc71",
            "warning": "#f1c40f",
            "error": "#e74c3c"
        }
        
        notification = QLabel(message)
        notification.setStyleSheet(f"""
            QLabel {{
                background-color: {colors.get(level, colors['info'])};
                color: white;
                padding: 10px;
                border-radius: 5px;
            }}
        """)
        
        # Aggiungi al layout principale
        self.status_layout.addWidget(notification)
        
        # Rimuovi dopo 3 secondi
        QTimer.singleShot(3000, lambda: notification.deleteLater())

    def update_folder_view(self):
        """Aggiorna la vista delle cartelle"""
        try:
            self.folder_list.clear()
            current_dir = self.dir_input.text()
            
            if not os.path.exists(current_dir):
                return
                
            folders = []
            for folder in os.listdir(current_dir):
                folder_path = os.path.join(current_dir, folder)
                if os.path.isdir(folder_path):
                    num_files = len([f for f in os.listdir(folder_path) 
                                   if f.lower().endswith(('.jpg', '.png', '.txt'))])
                    rating = self.rating_system.get_rating(folder)
                    folders.append({
                        'name': folder,
                        'path': folder_path,
                        'num_files': num_files,
                        'rating': rating
                    })
            
            # Aggiorna l'interfaccia
            for folder in sorted(folders, key=lambda x: (-x['rating'], -x['num_files'])):
                rating_stars = "★" * folder['rating']
                item_text = f"{folder['name']} ({folder['num_files']} files) {rating_stars}"
                item = QListWidgetItem(item_text)
                item.setData(Qt.UserRole, folder['path'])
                self.folder_list.addItem(item)
                
        except Exception as e:
            self.log_message(f"[ERROR] Failed to update folder view: {str(e)}")

    def update_analysis_view(self, analysis_text):
        """Aggiorna la vista dell'analisi"""
        try:
            # Formatta il testo dell'analisi
            formatted_text = ""
            sections = [
                "PATTERN ANALYSIS",
                "CREATIVE INTERPRETATION",
                "COLOR ANALYSIS",
                "TECHNICAL NOTES",
                "PROMPT 1",
                "PROMPT 2"
            ]
            
            for section in sections:
                section_start = analysis_text.find(section)
                if section_start != -1:
                    next_section = float('inf')
                    for s in sections:
                        pos = analysis_text.find(s, section_start + len(section))
                        if pos != -1 and pos < next_section:
                            next_section = pos
                            
                    section_content = analysis_text[
                        section_start:next_section if next_section != float('inf') else None
                    ]
                    formatted_text += f"\n{section}\n{'='*len(section)}\n{section_content.strip()}\n"
            
            self.analysis_text.setText(formatted_text)
            
        except Exception as e:
            self.log_message(f"[ERROR] Failed to update analysis view: {str(e)}")

    def handle_upscale(self, index):
        """Gestisce la richiesta di upscale per l'immagine selezionata"""
        try:
            if len(self.image_manager.selected_images) != 1:
                self.show_notification("Please select one image", "warning")
                return
                
            image_path = next(iter(self.image_manager.selected_images))
            message_id = self.image_manager.image_tracking.get(image_path, {}).get('message_id')
            buttons_data = self.image_manager.image_tracking.get(image_path, {}).get('buttons_data', {})
            
            button_custom_id = buttons_data.get(f"upscale_{index}")
            if not button_custom_id:
                self.show_notification("Upscale button not available", "warning")
                return

            self.show_generation_progress(True, f"Upscaling U{index}...")
            success = self.discord_client.send_upscale_command(
                self.config["CHANNEL_ID"],
                self.config["GUILD_ID"],
                message_id,
                index,
                button_custom_id
            )

            if success:
                self.log_message(f"[INFO] Upscale {index} command sent successfully")
            else:
                self.show_notification("Failed to send upscale command", "error")
                
        except Exception as e:
            self.log_message(f"[ERROR] Upscale failed: {str(e)}")
            self.show_notification("Upscale failed", "error")
        finally:
            self.show_generation_progress(False)

    def handle_variation(self, index):
        """Gestisce la richiesta di variazione per l'immagine selezionata"""
        try:
            if len(self.image_manager.selected_images) != 1:
                self.show_notification("Please select one image", "warning")
                return
                
            image_path = next(iter(self.image_manager.selected_images))
            message_id = self.image_manager.image_tracking.get(image_path, {}).get('message_id')
            
            if not message_id:
                self.show_notification("Variation not available for this image", "warning")
                return

            self.show_generation_progress(True, f"Generating variation V{index}...")
            success = self.discord_client.send_variation_command(
                self.config["CHANNEL_ID"],
                self.config["GUILD_ID"],
                message_id,
                index
            )

            if success:
                self.log_message(f"[INFO] Variation {index} command sent successfully")
            else:
                self.show_notification("Failed to send variation command", "error")
                
        except Exception as e:
            self.log_message(f"[ERROR] Variation failed: {str(e)}")
            self.show_notification("Variation failed", "error")
        finally:
            self.show_generation_progress(False)

    def analyze_selected_images(self):
        """Analizza le immagini selezionate usando Claude"""
        try:
            selected_images = self.image_manager.selected_images
            if not selected_images:
                self.show_notification("No images selected", "warning")
                return
                
            self.analyze_btn.setEnabled(False)
            self.show_generation_progress(True, "Analyzing images...")
            
            for image_path in selected_images:
                self.log_message(f"[INFO] Analyzing {os.path.basename(image_path)}")
                
                # Analizza l'immagine con Claude
                analysis_result = self.claude_client.analyze_image(image_path)
                if analysis_result:
                    # Aggiorna la vista dell'analisi
                    self.update_analysis_view(analysis_result)
                    
                    # Aggiorna il tracking
                    self.image_manager.image_tracking['analysis'][image_path] = analysis_result
                    
                    # Salva l'analisi
                    analysis_path = f"{os.path.splitext(image_path)[0]}_analysis.txt"
                    with open(analysis_path, 'w', encoding='utf-8') as f:
                        json.dump(analysis_result, f, indent=2, ensure_ascii=False)
                    
                    self.show_notification(f"Analysis completed for {os.path.basename(image_path)}", "info")
                else:
                    self.show_notification(f"Analysis failed for {os.path.basename(image_path)}", "error")
                    
            self.image_manager.save_tracking_state()
            
        except Exception as e:
            self.log_message(f"[ERROR] Analysis failed: {str(e)}")
            self.show_notification("Analysis failed", "error")
        finally:
            self.analyze_btn.setEnabled(True)
            self.show_generation_progress(False)

    def handle_new_image(self, image_path, sref, category, subcategory, message_id, buttons_data):
        """Gestisce l'arrivo di una nuova immagine"""
        try:
            # Aggiorna tracking
            self.image_manager.image_tracking["series"][sref] = self.image_manager.image_tracking["series"].get(sref, [])
            self.image_manager.image_tracking["series"][sref].append({
                'path': image_path,
                'message_id': message_id,
                'buttons_data': buttons_data,
                'category': category,
                'subcategory': subcategory
            })
            
            # Aggiorna metadati
            metadata = {
                "sref": sref,
                "category": category,
                "subcategory": subcategory,
                "message_id": message_id,
                "buttons_data": buttons_data,
                "created": datetime.now().isoformat()
            }
            self.file_manager.add_image_metadata(image_path, metadata)
            
            # Salva stato tracking
            self.image_manager.save_tracking_state()
            
            # Se la cartella corrente è quella dell'immagine, aggiorna la galleria
            if self.current_folder and os.path.dirname(image_path) == self.current_folder:
                self.gallery.load_folder(self.current_folder)
                
            # Mostra notifica
            self.show_notification(f"New image received: {os.path.basename(image_path)}", "info")
                
        except Exception as e:
            self.log_message(f"[ERROR] Failed to handle new image: {str(e)}")
            self.show_notification("Failed to process new image", "error")

class MessageTracker:
    def __init__(self):
        self.tracked_messages = {}
        self.active_generations = {}
        self.message_types = {
            "imagine": {},
            "upscale": {},
            "variation": {}
        }
        self.cleanup_interval = 3600  # 1 ora in secondi
        self.last_cleanup = time.time()

    def track_message(self, message_id, message_type, original_id=None, data=None):
        """Traccia un nuovo messaggio"""
        self.tracked_messages[message_id] = {
            "type": message_type,
            "original_id": original_id,
            "timestamp": datetime.now().isoformat(),
            "data": data or {},
            "status": "pending"
        }
        self.message_types[message_type][message_id] = self.tracked_messages[message_id]

    def update_status(self, message_id, status, additional_data=None):
        """Aggiorna lo stato di un messaggio"""
        if message_id in self.tracked_messages:
            self.tracked_messages[message_id]["status"] = status
            if additional_data:
                self.tracked_messages[message_id]["data"].update(additional_data)

    def get_message_chain(self, message_id):
        """Ottiene la catena di messaggi correlati"""
        chain = []
        current_id = message_id
        
        while current_id:
            if current_id in self.tracked_messages:
                chain.append(self.tracked_messages[current_id])
                current_id = self.tracked_messages[current_id].get("original_id")
            else:
                break
                
        return chain

    def cleanup_old_messages(self):
        """Pulisce i messaggi vecchi"""
        if time.time() - self.last_cleanup < self.cleanup_interval:
            return
            
        current_time = datetime.now()
        for message_id in list(self.tracked_messages.keys()):
            message_time = datetime.fromisoformat(
                self.tracked_messages[message_id]["timestamp"]
            )
            if (current_time - message_time).days > 1:  # Mantieni per 24 ore
                message_type = self.tracked_messages[message_id]["type"]
                del self.tracked_messages[message_id]
                if message_id in self.message_types[message_type]:
                    del self.message_types[message_type][message_id]
        
        self.last_cleanup = time.time()

class FileManager:
    def __init__(self, app_reference):
        self.app = app_reference
        self.metadata_file = os.path.join(app_reference.system_dir, "metadata.json")
        self.metadata = self.load_metadata()
        self.backup_interval = 3600  # 1 ora
        self.last_backup = time.time()

    def load_metadata(self):
        """Carica i metadati delle immagini"""
        try:
            if os.path.exists(self.metadata_file):
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {
                "images": {},
                "categories": {},
                "tags": {},
                "last_update": None
            }
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to load metadata: {str(e)}")
            return {
                "images": {},
                "categories": {},
                "tags": {},
                "last_update": None
            }

    def save_metadata(self):
        """Salva i metadati delle immagini"""
        try:
            self.metadata["last_update"] = datetime.now().isoformat()
            with open(self.metadata_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)
            
            # Verifica se è necessario il backup
            if time.time() - self.last_backup > self.backup_interval:
                self.create_backup()
                
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to save metadata: {str(e)}")

    def create_backup(self):
        """Crea un backup dei metadati e delle immagini importanti"""
        try:
            # Backup directory
            backup_dir = os.path.join(self.app.system_dir, "backups")
            os.makedirs(backup_dir, exist_ok=True)
            
            # Backup metadata
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(backup_dir, f"metadata_{timestamp}.json")
            shutil.copy2(self.metadata_file, backup_file)
            
            # Mantieni solo gli ultimi 5 backup
            backups = sorted(glob.glob(os.path.join(backup_dir, "metadata_*.json")))
            if len(backups) > 5:
                for old_backup in backups[:-5]:
                    os.remove(old_backup)
                    
            self.last_backup = time.time()
            self.app.log_message("[INFO] Backup created successfully")
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Backup failed: {str(e)}")

    def add_image_metadata(self, image_path, metadata):
        """Aggiunge o aggiorna i metadati di un'immagine"""
        try:
            image_id = os.path.basename(image_path)
            self.metadata["images"][image_id] = {
                "path": image_path,
                "created": datetime.now().isoformat(),
                "metadata": metadata
            }
            
            # Aggiorna categorie
            category = metadata.get("category")
            if category:
                self.metadata["categories"][category] = \
                    self.metadata["categories"].get(category, [])
                if image_id not in self.metadata["categories"][category]:
                    self.metadata["categories"][category].append(image_id)
            
            # Aggiorna tags
            tags = metadata.get("tags", [])
            for tag in tags:
                self.metadata["tags"][tag] = self.metadata["tags"].get(tag, [])
                if image_id not in self.metadata["tags"][tag]:
                    self.metadata["tags"][tag].append(image_id)
                    
            self.save_metadata()
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to add image metadata: {str(e)}")

    def cleanup_old_files(self):
        """Pulisce file temporanei e verifica integrità"""
        try:
            # Rimuovi file temporanei
            temp_dir = os.path.join(self.app.system_dir, "temp")
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir)
                os.makedirs(temp_dir)
                
            # Verifica integrità metadata
            for image_id, data in list(self.metadata["images"].items()):
                if not os.path.exists(data["path"]):
                    del self.metadata["images"][image_id]
                    # Rimuovi da categorie e tags
                    for category in self.metadata["categories"].values():
                        if image_id in category:
                            category.remove(image_id)
                    for tag_list in self.metadata["tags"].values():
                        if image_id in tag_list:
                            tag_list.remove(image_id)
                            
            self.save_metadata()
            self.app.log_message("[INFO] Cleanup completed")
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Cleanup failed: {str(e)}")

    def get_image_metadata(self, image_path):
        """Recupera i metadati di un'immagine"""
        try:
            image_id = os.path.basename(image_path)
            return self.metadata["images"].get(image_id, {}).get("metadata", {})
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to get image metadata: {str(e)}")
            return {}

    def add_image_tag(self, image_path, tag):
        """Aggiunge un tag a un'immagine"""
        try:
            image_id = os.path.basename(image_path)
            metadata = self.get_image_metadata(image_path)
            
            if "tags" not in metadata:
                metadata["tags"] = []
                
            if tag not in metadata["tags"]:
                metadata["tags"].append(tag)
                
            self.add_image_metadata(image_path, metadata)
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to add tag: {str(e)}")

class FileManager:
    def __init__(self, app_reference):
        self.app = app_reference
        self.metadata_file = os.path.join(app_reference.system_dir, "metadata.json")
        self.backup_dir = os.path.join(app_reference.system_dir, "backups")
        self.temp_dir = os.path.join(app_reference.system_dir, "temp")
        self.metadata = self.load_metadata()
        
        # Configurazione backup
        self.backup_interval = 3600  # 1 ora
        self.max_backups = 5
        self.last_backup = time.time()
        
        # Configurazione pulizia
        self.cleanup_interval = 86400  # 24 ore
        self.last_cleanup = time.time()
        
        # Inizializza struttura directory
        self._init_directories()

    def _init_directories(self):
        """Inizializza le directory necessarie"""
        directories = [
            self.backup_dir,
            self.temp_dir,
            os.path.dirname(self.metadata_file)
        ]
        for directory in directories:
            os.makedirs(directory, exist_ok=True)

    def load_metadata(self):
        """Carica i metadati con gestione errori migliorata"""
        try:
            if os.path.exists(self.metadata_file):
                with open(self.metadata_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Verifica integrità struttura
                    required_keys = ["images", "categories", "tags", "last_update"]
                    if all(key in data for key in required_keys):
                        return data
                    
            # Se il file non esiste o è corrotto, crea nuova struttura
            return self._create_default_metadata()
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Failed to load metadata: {str(e)}")
            return self._create_default_metadata()

    def _create_default_metadata(self):
        """Crea struttura metadati di default"""
        return {
            "images": {},
            "categories": {},
            "tags": {},
            "last_update": datetime.now().isoformat(),
            "backup_history": [],
            "version": "1.0.0"
        }

    def create_backup(self, force=False):
        """Crea backup dei metadati con rotazione"""
        try:
            current_time = time.time()
            
            # Verifica se è necessario il backup
            if not force and (current_time - self.last_backup < self.backup_interval):
                return
                
            # Crea nome file backup con timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_file = os.path.join(self.backup_dir, f"metadata_{timestamp}.json")
            
            # Salva backup corrente
            with open(backup_file, 'w', encoding='utf-8') as f:
                json.dump(self.metadata, f, indent=2, ensure_ascii=False)
            
            # Aggiorna storia backup
            self.metadata["backup_history"].append({
                "timestamp": timestamp,
                "file": backup_file,
                "size": os.path.getsize(backup_file)
            })
            
            # Rotazione backup
            self._rotate_backups()
            
            self.last_backup = current_time
            self.app.log_message("[INFO] Backup created successfully")
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Backup failed: {str(e)}")

    def _rotate_backups(self):
        """Gestisce la rotazione dei backup mantenendo solo gli ultimi N"""
        try:
            # Ordina backup per data
            backups = sorted(
                glob.glob(os.path.join(self.backup_dir, "metadata_*.json")),
                key=os.path.getmtime
            )
            
            # Rimuovi backup più vecchi
            while len(backups) > self.max_backups:
                oldest = backups.pop(0)
                os.remove(oldest)
                # Aggiorna storia backup nei metadati
                self.metadata["backup_history"] = [
                    b for b in self.metadata["backup_history"] 
                    if b["file"] != oldest
                ]
                
        except Exception as e:
            self.app.log_message(f"[ERROR] Backup rotation failed: {str(e)}")

    def cleanup_temp_files(self):
        """Pulisce i file temporanei e verifica integrità"""
        try:
            current_time = time.time()
            
            # Verifica intervallo pulizia
            if current_time - self.last_cleanup < self.cleanup_interval:
                return
                
            # Pulisci directory temp
            for file in os.listdir(self.temp_dir):
                file_path = os.path.join(self.temp_dir, file)
                try:
                    if os.path.isfile(file_path):
                        os.unlink(file_path)
                    elif os.path.isdir(file_path):
                        shutil.rmtree(file_path)
                except Exception as e:
                    self.app.log_message(f"[ERROR] Failed to remove {file_path}: {str(e)}")
            
            # Verifica integrità metadati
            self._verify_metadata_integrity()
            
            self.last_cleanup = current_time
            self.app.log_message("[INFO] Cleanup completed successfully")
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Cleanup failed: {str(e)}")

    def _verify_metadata_integrity(self):
        """Verifica e ripara integrità dei metadati"""
        try:
            # Verifica immagini esistenti
            for image_id in list(self.metadata["images"].keys()):
                image_data = self.metadata["images"][image_id]
                if not os.path.exists(image_data["path"]):
                    # Rimuovi riferimenti a immagini non esistenti
                    del self.metadata["images"][image_id]
                    
                    # Pulisci categorie
                    for category in self.metadata["categories"].values():
                        if image_id in category:
                            category.remove(image_id)
                    
                    # Pulisci tags
                    for tag_list in self.metadata["tags"].values():
                        if image_id in tag_list:
                            tag_list.remove(image_id)
            
            # Rimuovi categorie e tag vuoti
            self.metadata["categories"] = {
                k: v for k, v in self.metadata["categories"].items() if v
            }
            self.metadata["tags"] = {
                k: v for k, v in self.metadata["tags"].items() if v
            }
            
            # Salva metadati puliti
            self.save_metadata()
            
        except Exception as e:
            self.app.log_message(f"[ERROR] Metadata integrity check failed: {str(e)}")
            
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MidjourneyStudioApp()
    window.show()
    sys.exit(app.exec_())