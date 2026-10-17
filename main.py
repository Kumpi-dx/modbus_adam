import sys
import configparser
import pyodbc
from datetime import datetime
import serial.tools.list_ports

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                                QPushButton, QComboBox, QTextEdit, QLabel, QHBoxLayout,
                                QGroupBox, QGridLayout, QMessageBox, QCheckBox)
from PySide6.QtCore import QThread, Signal, Qt
from pymodbus.client import ModbusSerialClient

# ==========================================
# THREAD WORKER UNTUK MODBUS & DATABASE
# ==========================================
class ModbusWorker(QThread):
    data_received = Signal(list)
    log_msg = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.running = False
        self.client = None
        self.db_conn = None
        self.last_data = None 

    def connect_db(self):
        try:
            # Koneksi khusus SQL Server via pyodbc
            conn_str = (
                f"DRIVER={{ODBC Driver 17 for SQL Server}};"
                f"SERVER={self.config['DATABASE']['server']};"
                f"DATABASE={self.config['DATABASE']['database']};"
                f"UID={self.config['DATABASE']['username']};"
                f"PWD={self.config['DATABASE']['password']}"
            )
            self.db_conn = pyodbc.connect(conn_str)
            self.log_msg.emit("Database terhubung: db_modbus")
        except Exception as e:
            self.log_msg.emit(f"Gagal koneksi database: {str(e)}")

    def save_to_db(self, data):
        if not self.db_conn:
            return
        try:
            cursor = self.db_conn.cursor()
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            val = (timestamp, *data)

            cursor.execute("SELECT COUNT(*) FROM tb_analog_log")
            count = cursor.fetchone()[0]

            if count == 0:
                query = """
                    INSERT INTO tb_analog_log (log_time, ai0, ai1, ai2, ai3, ai4, ai5, ai6, ai7) 
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """
                cursor.execute(query, val)
            else:
                query = """
                    UPDATE tb_analog_log 
                    SET log_time = ?, ai0 = ?, ai1 = ?, ai2 = ?, ai3 = ?, ai4 = ?, ai5 = ?, ai6 = ?, ai7 = ?
                    WHERE log_time = (SELECT MAX(log_time) FROM tb_analog_log)
                """
                cursor.execute(query, val)

            self.db_conn.commit()
            cursor.close()
        except Exception as e:
            self.log_msg.emit(f"Error Simpan DB: {str(e)}")

    def run(self):
        self.running = True
        slave_id = int(self.config['MODBUS']['slave_id'])
        
        # 1. Inisialisasi TANPA argumen 'slave'
        self.client = ModbusSerialClient(
            port=self.config['MODBUS']['port'],
            baudrate=int(self.config['MODBUS']['baudrate']),
            timeout=int(self.config['MODBUS']['timeout'])
        )
        
        if not self.client.connect():
            self.log_msg.emit(f"Gagal membuka port {self.config['MODBUS']['port']}.")
            return

        self.connect_db()
        refresh_rate = int(self.config['MODBUS']['refresh_rate_ms'])

        self.log_msg.emit("Mulai scanning Modbus...")

        while self.running:
            try:
                self.client.slave = slave_id 
                
                result = self.client.read_holding_registers(address=0, count=8)
                
                if not result.isError():
                    current_data = result.registers
                    self.data_received.emit(current_data)
                    
                    if current_data != self.last_data:
                        self.save_to_db(current_data)
                        self.last_data = current_data
                        self.log_msg.emit(f"[{datetime.now().strftime('%H:%M:%S')}] Data berhasil disimpan!")
                else:
                    self.log_msg.emit("Modbus Error: Gagal membaca register.")
                    
            except Exception as e:
                self.log_msg.emit(f"Exception Modbus: {str(e)}")
            
            QThread.msleep(refresh_rate)

    def stop(self):
        self.running = False
        if self.client:
            self.client.close()
        if self.db_conn:
            self.db_conn.close()
        self.quit()
        self.wait()

# ==========================================
# WINDOW UTAMA APLIKASI (UI GUI)
# ==========================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Data Acquisition System - Modbus RTU")
        self.setMinimumSize(700, 500)
        
        # Load Config
        self.config = configparser.ConfigParser()
        self.config.read('config.ini')
        self.worker = None
        
        self.setup_ui()
        self.showMinimized()
        if self.chk_auto_connect.isChecked():
            self.toggle_connection()

    def setup_ui(self):
        # Mengatur tema gelap (Dark Theme) untuk aplikasi
        self.setStyleSheet("""
            QMainWindow { background-color: #252525; }
            
            QGroupBox { 
                color: #FFFFFF; 
                font-weight: bold; 
                border: 1px solid #444; 
                margin-top: 10px; 
                padding: 10px;
                background-color: #2D2D2D;
            }
            
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }

            QLabel { color: #E0E0E0; font-size: 13px; }
            
            QComboBox {
                background-color: #3D3D3D;
                color: white;
                border: 1px solid #555;
                padding: 5px;
            }

            QPushButton { 
                background-color: #4A4A4A; 
                color: white; 
                border: none; 
                padding: 5px 10px; 
                font-weight: bold;
            }
            QPushButton:hover { background-color: #606060; }
            
            /* Style Khusus Tombol Connect */
            #btn_connect_style { background-color: #007ACC; }
            #btn_connect_style:hover { background-color: #005F9E; }
            
            QTextEdit { 
                background-color: #1A1A1A; 
                color: #00FF00; 
                border: 1px solid #333; 
                font-family: 'Consolas';
            }
        """)

        main_layout = QVBoxLayout()
        
        # --- HEADER ---
        header = QLabel("MODBUS SERIAL MONITORING & DATA LOGGING")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: #00AEEF; margin-bottom: 10px;")
        header.setAlignment(Qt.AlignCenter)
        main_layout.addWidget(header)
        
        # --- SERIAL SETTINGS ---
        group_serial = QGroupBox("Communication Control")
        layout_serial = QHBoxLayout()
        self.port_combo = QComboBox()
        self.scan_ports()
        
        self.btn_connect = QPushButton("CONNECT")
        self.btn_connect.setObjectName("btn_connect_style")
        self.btn_connect.setMinimumHeight(40)
        self.btn_connect.clicked.connect(self.toggle_connection)
        
        layout_serial.addWidget(QLabel("COM Port:"))
        layout_serial.addWidget(self.port_combo)
        layout_serial.addWidget(self.btn_connect)
        group_serial.setLayout(layout_serial)
        main_layout.addWidget(group_serial)

        # --- AUTO CONNECT OPTION ---
        self.chk_auto_connect = QCheckBox("Auto Connect saat dibuka")
        self.chk_auto_connect.setChecked(
            self.config.getboolean('APP', 'auto_connect', fallback=True)
        )
        self.chk_auto_connect.setStyleSheet("color: #E0E0E0;")
        self.chk_auto_connect.stateChanged.connect(self.save_auto_connect)
        main_layout.addWidget(self.chk_auto_connect)

        # --- ANALOG INPUTS (HMI STYLE) ---
        group_ai = QGroupBox("Live Analog Values")
        layout_ai = QGridLayout()
        
        self.ai_labels = []
        for i in range(8):
            # Panel nilai digital dengan tampilan LED
            panel = QWidget()
            panel_layout = QVBoxLayout()
            label_name = QLabel(f"AI-{i}")
            label_name.setStyleSheet("font-size: 10px; color: #888;")
            
            val_display = QLabel("0.000 mA")
            val_display.setStyleSheet("""
                background-color: black; 
                color: #00FF00; 
                font-family: 'Courier New'; 
                font-size: 18px; 
                font-weight: bold;
                border: 2px solid #333;
                padding: 5px;
            """)
            val_display.setAlignment(Qt.AlignCenter)
            
            panel_layout.addWidget(label_name)
            panel_layout.addWidget(val_display)
            panel.setLayout(panel_layout)
            
            self.ai_labels.append(val_display)
            layout_ai.addWidget(panel, i // 4, i % 4)
            
        group_ai.setLayout(layout_ai)
        main_layout.addWidget(group_ai)

        # --- LOGGING AREA ---
        group_log = QGroupBox("System Log")
        layout_log = QVBoxLayout()
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumHeight(150)
        layout_log.addWidget(self.log_output)
        group_log.setLayout(layout_log)
        main_layout.addWidget(group_log)

        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def scan_ports(self):
        self.port_combo.clear()
        ports = serial.tools.list_ports.comports()
        for port in ports:
            self.port_combo.addItem(port.device)
            
        # Set default port dari config jika tersedia di sistem
        default_port = self.config.get('MODBUS', 'port', fallback='')
        index = self.port_combo.findText(default_port)
        if index >= 0:
            self.port_combo.setCurrentIndex(index)

    def toggle_connection(self):
        if self.worker is None or not self.worker.isRunning():
            # Update config port dari UI sebelum menjalankan thread
            self.config['MODBUS']['port'] = self.port_combo.currentText()
            
            self.worker = ModbusWorker(self.config)
            self.worker.data_received.connect(self.update_ai_data)
            self.worker.log_msg.connect(self.append_log)
            self.worker.start()
            
            self.btn_connect.setText("Disconnect / Stop Scan")
            self.btn_connect.setStyleSheet("font-weight: bold; color: red;")
            self.port_combo.setEnabled(False)
        else:
            self.worker.stop()
            self.worker = None
            
            self.btn_connect.setText("Connect / Start Scan")
            self.btn_connect.setStyleSheet("font-weight: bold; color: black;")
            self.port_combo.setEnabled(True)
            self.append_log("Koneksi ditutup.")

    def save_auto_connect(self):
        if not self.config.has_section('APP'):
            self.config.add_section('APP')
        self.config['APP']['auto_connect'] = str(self.chk_auto_connect.isChecked())
        try:
            with open('config.ini', 'w') as f:
                self.config.write(f)
        except Exception as e:
            self.append_log(f"Gagal simpan config: {str(e)}")

    def update_ai_data(self, data):
        # Update UI dengan nilai raw. 
        # Jika perlu konversi ke mA, lakukan operasi matematis di sini sebelum ditampilkan.
        for i, val in enumerate(data):
            self.ai_labels[i].setText(f"AI{i}: {val}")

    def append_log(self, msg):
        self.log_output.append(msg)
        # Scroll otomatis ke bawah
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def closeEvent(self, event):
        msg = QMessageBox(self)
        msg.setWindowTitle('Konfirmasi Keluar')
        msg.setText("Apakah Anda yakin ingin menutup aplikasi?")
        msg.setInformativeText("Koneksi Modbus akan diputus.")
        msg.setIcon(QMessageBox.Question)
        msg.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        msg.setDefaultButton(QMessageBox.No)
        
        # Paksa style agar tulisan terbaca (Putih di atas latar gelap)
        msg.setStyleSheet("""
            QMessageBox { background-color: #2D2D2D; }
            QLabel { color: #FFFFFF; font-size: 14px; }
            QPushButton { background-color: #444; color: white; padding: 5px 15px; }
        """)

        reply = msg.exec()

        if reply == QMessageBox.Yes:
            self.save_auto_connect()
            if self.worker:
                self.worker.stop()
            event.accept()
        else:
            event.ignore()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())