import sys
import configparser
import pyodbc
from datetime import datetime
import serial.tools.list_ports

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QPushButton, QComboBox, QTextEdit, QLabel, QHBoxLayout,
                               QGroupBox, QGridLayout)
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
        self.last_data = None  # Menyimpan data pembacaan terakhir

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
            query = """
                INSERT INTO tb_analog_log (log_time, ai0, ai1, ai2, ai3, ai4, ai5, ai6, ai7) 
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """
            # Format timestamp milidetik: YYYY-MM-DD HH:MM:SS.mmm
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
            
            # Gabungkan timestamp dan data dari Modbus
            val = (timestamp, *data)
            
            cursor.execute(query, val)
            self.db_conn.commit()
            cursor.close()
        except Exception as e:
            self.log_msg.emit(f"Error Insert DB: {str(e)}")

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
                # 2. Coba set slave secara manual ke objek client (beberapa versi 3.x menggunakan ini)
                # Jika ini error, hapus baris di bawah ini
                self.client.slave = slave_id 
                
                # 3. Baca tanpa parameter slave/unit
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

    def setup_ui(self):
        main_layout = QVBoxLayout()
        
        # --- GROUP: SERIAL PORT SETTINGS ---
        group_serial = QGroupBox("Serial Port Settings")
        layout_serial = QHBoxLayout()
        
        self.port_combo = QComboBox()
        self.scan_ports()
        
        self.btn_connect = QPushButton("Connect / Start Scan")
        self.btn_connect.setMinimumHeight(40)
        self.btn_connect.setStyleSheet("font-weight: bold;")
        self.btn_connect.clicked.connect(self.toggle_connection)
        
        layout_serial.addWidget(QLabel("Pilih COM Port:"))
        layout_serial.addWidget(self.port_combo)
        layout_serial.addWidget(self.btn_connect)
        group_serial.setLayout(layout_serial)
        main_layout.addWidget(group_serial)

        # --- GROUP: ANALOG INPUT AREA ---
        group_ai = QGroupBox("Analog Input Area (AI0 - AI7)")
        layout_ai = QGridLayout()
        
        self.ai_labels = []
        for i in range(8):
            lbl = QLabel(f"AI{i}: 0.000")
            lbl.setStyleSheet("""
                color: #00FF00; 
                background-color: black; 
                padding: 10px; 
                font-family: Consolas; 
                font-size: 14px;
                border: 1px solid gray;
            """)
            lbl.setAlignment(Qt.AlignCenter)
            self.ai_labels.append(lbl)
            
            # Susun dalam 2 kolom (0-3 di kiri, 4-7 di kanan)
            row = i % 4
            col = i // 4
            layout_ai.addWidget(lbl, row, col)
            
        group_ai.setLayout(layout_ai)
        main_layout.addWidget(group_ai)

        # --- GROUP: INFORMATION OUTPUT ---
        group_log = QGroupBox("Information Output Log")
        layout_log = QVBoxLayout()
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setStyleSheet("background-color: #FAFAFA; color: #333; font-family: Consolas;")
        
        layout_log.addWidget(self.log_output)
        group_log.setLayout(layout_log)
        main_layout.addWidget(group_log)

        # Set Central Widget
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
        # Memastikan thread Modbus dihentikan saat aplikasi ditutup (X)
        if self.worker:
            self.worker.stop()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())