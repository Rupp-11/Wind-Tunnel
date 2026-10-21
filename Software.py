from __future__ import annotations

import math
import queue
import threading
import time

from dataclasses import dataclass
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import serial
from serial import SerialException
from serial.tools import list_ports

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D


# ============================================================
# CONFIGURAÇÕES
# ============================================================

BAUDRATE = 115200

# Limites fixos do gráfico Força × Velocidade
GRAPH_MAX_SPEED_KMH = 100.0
GRAPH_MAX_FORCE_N = 0.1

MAX_TEMP_C = 250.0

DEFAULT_SMOKE_MS = 300

MIN_SMOKE_MS = 50
MAX_SMOKE_MS = 5000

# Cálculo do Cd: ignora velocidades baixas (onde a força residual/offset
# da célula de carga distorce o resultado) e usa um ajuste em vez de média.
CD_MIN_SPEED_KMH = 20.0
CD_FIT_WITH_OFFSET = True   # True: F = a*v² + b | False: F = a*v²


def compute_cd(samples, density, area):
    """Cd a partir do ajuste por mínimos quadrados de F x v².

    Em vez de calcular o Cd de cada ponto e tirar a média (o que é
    dominado pelos pontos de baixa velocidade), ajusta a reta
    F = a*v² + b usando só pontos acima de CD_MIN_SPEED_KMH.
    O termo b absorve a força residual (offset). Cd = 2*a / (rho*A).
    """
    pts = []

    for sample in samples:

        if sample.speed_kmh < CD_MIN_SPEED_KMH:
            continue

        v = sample.speed_kmh / 3.6

        pts.append((v * v, abs(sample.force_n)))

    if len(pts) < 3:
        return None

    n = len(pts)

    if CD_FIT_WITH_OFFSET:

        sx = sum(x for x, _ in pts)
        sy = sum(y for _, y in pts)
        sxx = sum(x * x for x, _ in pts)
        sxy = sum(x * y for x, y in pts)

        den = n * sxx - sx * sx

        if den > 0:

            a = (n * sxy - sx * sy) / den

            if a > 0:

                cd = 2.0 * a / (density * area)

                if math.isfinite(cd):
                    return cd

    # Sem offset (ou se o ajuste com offset falhar): F = a*v²
    sxx = sum(x * x for x, _ in pts)
    sxy = sum(x * y for x, y in pts)

    if sxx <= 0:
        return None

    a = sxy / sxx

    if a <= 0:
        return None

    cd = 2.0 * a / (density * area)

    return cd if math.isfinite(cd) else None


# ============================================================
# AMOSTRA
# ============================================================

@dataclass
class Sample:

    timestamp_ms: float = 0.0
    pwm: float = 0.0
    rpm: float = 0.0
    speed_kmh: float = 0.0
    force_n: float = 0.0
    temperature_c: float = 0.0

    fan: int = 0
    led: int = 0
    smoke: int = 0


# ============================================================
# APLICAÇÃO
# ============================================================

class WindTunnelApp:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "WIND TUNNEL 2.0"
        )

        self.root.geometry(
            "1450x850"
        )

        self.root.minsize(
            1100,
            700
        )


        # ====================================================
        # SERIAL
        # ====================================================

        self.ser = None

        self.serial_thread = None

        self.serial_running = False

        self.serial_queue = queue.Queue()


        # ====================================================
        # DADOS
        # ====================================================

        self.samples = []

        # Comparação dupla: primeiro carrinho (azul) e segundo (vermelho).
        self.double_mode_var = tk.BooleanVar(value=False)
        self.object2_name_var = tk.StringVar(value="Carrinho Y")
        self.area2_var = tk.StringVar(value="0.025")
        self.comparison_stage = None  # None=sem comparação, 0=primeiro, 1=segundo, 2=concluído
        self.comparison_samples = [[], []]

        self.latest_sample = None

        self.lock = threading.Lock()


        # ====================================================
        # CONTROLE DE AQUISIÇÃO
        # ====================================================

        self.test_running = False

        self.acquisition_active = False

        self.last_fan_state = False


        # ====================================================
        # MODO DE CONTROLE
        # ====================================================

        # False = computador controla
        # True  = potenciometro controla

        self.manual_mode = False


        # ====================================================
        # ESTADO DO AQUECIMENTO
        # ====================================================

        self.heater_enabled = True

        self.target_temperature = 220.0


        # ====================================================
        # VARIÁVEIS
        # ====================================================

        self.com_var = tk.StringVar()

        self.status_var = tk.StringVar(
            value="Desconectado"
        )


        self.speed_var = tk.StringVar(
            value="0.0 km/h"
        )

        self.force_var = tk.StringVar(
            value="0.000 N"
        )

        self.rpm_var = tk.StringVar(
            value="0 RPM"
        )

        self.temp_var = tk.StringVar(
            value="0.0 °C"
        )


        self.motor_var = tk.StringVar(
            value="DESLIGADO"
        )

        self.led_var = tk.StringVar(
            value="DESLIGADA"
        )

        self.smoke_var = tk.StringVar(
            value="OFF"
        )


        self.target_temp_var = tk.StringVar(
            value="220.0"
        )

        self.heater_status_var = tk.StringVar(
            value="LIGADO"
        )

        self.heater_info_var = tk.StringVar(
            value="Alvo: 220.0 °C | Aquecimento: LIGADO"
        )


        self.smoke_duration_var = tk.StringVar(
            value=str(DEFAULT_SMOKE_MS)
        )


        self.object_name_var = tk.StringVar()

        self.area_var = tk.StringVar(
            value="0.0018"
        )

        self.density_var = tk.StringVar(
            value="1.225"
        )

        self.cd_var = tk.StringVar(
            value="--"
        )


        self.pwm_var = tk.StringVar(
            value="PWM: 0"
        )


        self.mode_var = tk.StringVar(
            value="MODO PC"
        )


        self.test_status_var = tk.StringVar(
            value="Nenhum teste em andamento"
        )


        # ====================================================
        # ESTILOS
        # ====================================================

        self.style = ttk.Style()


        self.style.configure(
            "Title.TLabel",
            font=("Arial", 20, "bold")
        )


        self.style.configure(
            "Value.TLabel",
            font=("Arial", 20, "bold")
        )


        self.style.configure(
            "TempNormal.TLabel",
            font=("Arial", 20, "bold")
        )


        self.style.configure(
            "TempHigh.TLabel",
            font=("Arial", 20, "bold"),
            foreground="#d00000"
        )


        self.style.configure(
            "Alert.TLabel",
            font=("Arial", 13, "bold"),
            foreground="#d00000"
        )


        self.style.configure(
            "Mode.TLabel",
            font=("Arial", 12, "bold")
        )


        self.style.configure(
            "Test.TLabel",
            font=("Arial", 12, "bold")
        )


        # ====================================================
        # UI
        # ====================================================

        self.build_ui()


        self.refresh_ports()


        self.root.after(
            100,
            self.process_serial_queue
        )


        self.root.after(
            250,
            self.update_interface
        )


        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close
        )


    # ========================================================
    # INTERFACE
    # ========================================================

    def build_ui(self):

        main = ttk.Frame(
            self.root,
            padding=10
        )

        main.pack(
            fill="both",
            expand=True
        )


        # ====================================================
        # COLUNA ESQUERDA
        # ====================================================

        left_container = ttk.Frame(main)

        left_container.pack(
            side="left",
            fill="y",
            padx=(0, 10)
        )


        canvas = tk.Canvas(
            left_container,
            width=390,
            highlightthickness=0
        )


        scrollbar = ttk.Scrollbar(
            left_container,
            orient="vertical",
            command=canvas.yview
        )


        scroll_frame = ttk.Frame(canvas)


        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(
                scrollregion=canvas.bbox("all")
            )
        )


        canvas.create_window(
            (0, 0),
            window=scroll_frame,
            anchor="nw"
        )


        canvas.configure(
            yscrollcommand=scrollbar.set
        )


        canvas.pack(
            side="left",
            fill="y",
            expand=True
        )


        scrollbar.pack(
            side="right",
            fill="y"
        )


        # ====================================================
        # COMUNICAÇÃO
        # ====================================================

        connection_frame = ttk.LabelFrame(
            scroll_frame,
            text="Comunicação serial",
            padding=10
        )

        connection_frame.pack(
            fill="x",
            pady=5
        )


        ttk.Label(
            connection_frame,
            text="Porta:"
        ).grid(
            row=0,
            column=0,
            sticky="w"
        )


        self.com_combo = ttk.Combobox(
            connection_frame,
            textvariable=self.com_var,
            state="readonly",
            width=15
        )


        self.com_combo.grid(
            row=0,
            column=1,
            padx=5
        )


        ttk.Button(
            connection_frame,
            text="Atualizar",
            command=self.refresh_ports
        ).grid(
            row=0,
            column=2
        )


        self.connect_button = ttk.Button(
            connection_frame,
            text="Conectar",
            command=self.toggle_connection
        )


        self.connect_button.grid(
            row=1,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=5
        )


        ttk.Label(
            connection_frame,
            textvariable=self.status_var
        ).grid(
            row=2,
            column=0,
            columnspan=3
        )


        ttk.Button(
            connection_frame,
            text="PING",
            command=lambda: self.send("PING")
        ).grid(
            row=3,
            column=0,
            columnspan=3,
            sticky="ew",
            pady=3
        )


        # ====================================================
        # MONITOR
        # ====================================================

        monitor = ttk.LabelFrame(
            scroll_frame,
            text="Monitor",
            padding=10
        )

        monitor.pack(
            fill="x",
            pady=5
        )


        ttk.Label(
            monitor,
            text="VELOCIDADE"
        ).pack()


        ttk.Label(
            monitor,
            textvariable=self.speed_var,
            style="Value.TLabel"
        ).pack(
            pady=(0, 10)
        )


        ttk.Label(
            monitor,
            text="FORÇA"
        ).pack()


        ttk.Label(
            monitor,
            textvariable=self.force_var,
            style="Value.TLabel"
        ).pack(
            pady=(0, 10)
        )


        ttk.Label(
            monitor,
            text="RPM"
        ).pack()


        ttk.Label(
            monitor,
            textvariable=self.rpm_var,
            style="Value.TLabel"
        ).pack(
            pady=(0, 10)
        )


        ttk.Label(
            monitor,
            text="TEMPERATURA"
        ).pack()


        self.temp_label = ttk.Label(
            monitor,
            textvariable=self.temp_var,
            style="TempNormal.TLabel"
        )


        self.temp_label.pack()


        self.temp_alert_label = ttk.Label(
            monitor,
            text="",
            style="Alert.TLabel"
        )


        self.temp_alert_label.pack(
            pady=(2, 10)
        )



        # ====================================================
        # CONTROLE DO FAN
        # ====================================================

        fan_frame = ttk.LabelFrame(
            scroll_frame,
            text="Controle do FAN",
            padding=10
        )


        fan_frame.pack(
            fill="x",
            pady=5
        )


        # ====================================================
        # MODO
        # ====================================================

        ttk.Label(
            fan_frame,
            text="Modo de controle:"
        ).pack()


        ttk.Label(
            fan_frame,
            textvariable=self.mode_var,
            style="Mode.TLabel"
        ).pack(
            pady=(0, 5)
        )


        self.manual_mode_button = ttk.Button(
            fan_frame,
            text="ATIVAR MODO MANUAL",
            command=self.toggle_manual_mode
        )


        self.manual_mode_button.pack(
            fill="x",
            pady=3
        )


        ttk.Label(
            fan_frame,
            text="No modo manual o potenciômetro controla o FAN."
        ).pack(
            pady=(0, 8)
        )


        # ====================================================
        # PWM
        # ====================================================

        ttk.Label(
            fan_frame,
            text="PWM 0–255"
        ).pack()


        self.pwm_scale = tk.Scale(
            fan_frame,
            from_=0,
            to=255,
            orient="horizontal",
            resolution=1,
            command=self.on_pwm_slider
        )


        self.pwm_scale.pack(
            fill="x"
        )


        self.pwm_scale.set(0)


        ttk.Label(
            fan_frame,
            textvariable=self.pwm_var
        ).pack()


        buttons = ttk.Frame(
            fan_frame
        )


        buttons.pack(
            fill="x",
            pady=5
        )


        ttk.Button(
            buttons,
            text="LIGAR FAN",
            command=self.start_fan
        ).pack(
            side="left",
            expand=True,
            fill="x",
            padx=2
        )


        ttk.Button(
            buttons,
            text="PARAR FAN",
            command=self.stop_fan
        ).pack(
            side="left",
            expand=True,
            fill="x",
            padx=2
        )


        # ====================================================
        # LUZ
        # ====================================================

        led_frame = ttk.LabelFrame(
            scroll_frame,
            text="Luz do túnel",
            padding=10
        )


        led_frame.pack(
            fill="x",
            pady=5
        )


        led_buttons = ttk.Frame(
            led_frame
        )


        led_buttons.pack(
            fill="x"
        )


        ttk.Button(
            led_buttons,
            text="LIGAR",
            command=lambda: self.set_led(True)
        ).pack(
            side="left",
            expand=True,
            fill="x",
            padx=2
        )


        ttk.Button(
            led_buttons,
            text="DESLIGAR",
            command=lambda: self.set_led(False)
        ).pack(
            side="left",
            expand=True,
            fill="x",
            padx=2
        )


        # ====================================================
        # FUMAÇA
        # ====================================================

        smoke_frame = ttk.LabelFrame(
            scroll_frame,
            text="Bomba de fumaça",
            padding=10
        )


        smoke_frame.pack(
            fill="x",
            pady=5
        )


        ttk.Label(
            smoke_frame,
            text="Duração do pulso (ms):"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            smoke_frame,
            textvariable=self.smoke_duration_var
        ).pack(
            fill="x",
            pady=3
        )


        ttk.Button(
            smoke_frame,
            text="PULSO DE FUMAÇA",
            command=self.smoke_pulse
        ).pack(
            fill="x"
        )


        # ====================================================
        # AQUECIMENTO / TERMOSTATO
        # ====================================================

        heater_frame = ttk.LabelFrame(
            scroll_frame,
            text="Aquecimento / Termostato",
            padding=10
        )


        heater_frame.pack(
            fill="x",
            pady=5
        )


        ttk.Label(
            heater_frame,
            text="Temperatura alvo (0–250 °C):"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            heater_frame,
            textvariable=self.target_temp_var
        ).pack(
            fill="x",
            pady=3
        )


        ttk.Button(
            heater_frame,
            text="SETAR TEMPERATURA",
            command=self.set_temperature
        ).pack(
            fill="x",
            pady=3
        )


        heater_buttons = ttk.Frame(
            heater_frame
        )


        heater_buttons.pack(
            fill="x",
            pady=3
        )


        ttk.Button(
            heater_buttons,
            text="LIGAR AQUECIMENTO",
            command=self.enable_heater
        ).pack(
            side="left",
            expand=True,
            fill="x",
            padx=2
        )


        ttk.Button(
            heater_buttons,
            text="DESLIGAR",
            command=self.disable_heater
        ).pack(
            side="left",
            expand=True,
            fill="x",
            padx=2
        )


        ttk.Separator(
            heater_frame,
            orient="horizontal"
        ).pack(
            fill="x",
            pady=6
        )


        ttk.Label(
            heater_frame,
            textvariable=self.heater_info_var,
            style="Mode.TLabel"
        ).pack()


        # ====================================================
        # TESTE AUTOMÁTICO
        # ====================================================

        test_frame = ttk.LabelFrame(
            scroll_frame,
            text="Teste automático",
            padding=10
        )


        test_frame.pack(
            fill="x",
            pady=5
        )


        self.test_duration_var = tk.StringVar(
            value="30000"
        )


        self.test_start_pwm_var = tk.StringVar(
            value="30"
        )


        self.test_end_pwm_var = tk.StringVar(
            value="255"
        )


        ttk.Label(
            test_frame,
            text="Duração (ms)"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            test_frame,
            textvariable=self.test_duration_var
        ).pack(
            fill="x"
        )


        ttk.Label(
            test_frame,
            text="PWM inicial"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            test_frame,
            textvariable=self.test_start_pwm_var
        ).pack(
            fill="x"
        )


        ttk.Label(
            test_frame,
            text="PWM final"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            test_frame,
            textvariable=self.test_end_pwm_var
        ).pack(
            fill="x"
        )


        ttk.Button(
            test_frame,
            text="INICIAR TESTE",
            command=self.start_test
        ).pack(
            fill="x",
            pady=3
        )


        ttk.Button(
            test_frame,
            text="PARAR TESTE",
            command=self.stop_test
        ).pack(
            fill="x"
        )


        self.test_status_label = ttk.Label(
            test_frame,
            textvariable=self.test_status_var,
            style="Test.TLabel"
        )


        self.test_status_label.pack(
            pady=8
        )


        # ====================================================
        # DADOS / CD
        # ====================================================

        data_frame = ttk.LabelFrame(
            scroll_frame,
            text="Dados / Coeficiente de arrasto",
            padding=10
        )


        data_frame.pack(
            fill="x",
            pady=5
        )


        ttk.Button(
            data_frame,
            text="TARA",
            command=self.tare
        ).pack(
            fill="x",
            pady=2
        )


        ttk.Button(
            data_frame,
            text="LIMPAR DADOS",
            command=self.clear_data
        ).pack(
            fill="x",
            pady=2
        )


        ttk.Button(
            data_frame,
            text="SALVAR GRÁFICO",
            command=self.save_graph
        ).pack(
            fill="x",
            pady=2
        )


        ttk.Label(
            data_frame,
            text="Objeto:"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            data_frame,
            textvariable=self.object_name_var
        ).pack(
            fill="x"
        )


        ttk.Label(
            data_frame,
            text="Área frontal (m²):"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            data_frame,
            textvariable=self.area_var
        ).pack(
            fill="x"
        )

        ttk.Checkbutton(
            data_frame,
            text="Modo de comparação dupla (2 carrinhos)",
            variable=self.double_mode_var
        ).pack(anchor="w", pady=(8, 2))

        ttk.Label(data_frame, text="Segundo carrinho — nome:").pack(anchor="w")
        ttk.Entry(data_frame, textvariable=self.object2_name_var).pack(fill="x")

        ttk.Label(data_frame, text="Segundo carrinho — área frontal (m²):").pack(anchor="w")
        ttk.Entry(data_frame, textvariable=self.area2_var).pack(fill="x")

        ttk.Label(
            data_frame,
            text="Densidade do ar (kg/m³):"
        ).pack(
            anchor="w"
        )


        ttk.Entry(
            data_frame,
            textvariable=self.density_var
        ).pack(
            fill="x"
        )


        ttk.Button(
            data_frame,
            text="CALCULAR Cd",
            command=self.calculate_cd
        ).pack(
            fill="x",
            pady=5
        )


        ttk.Label(
            data_frame,
            text="Cd:"
        ).pack()


        ttk.Label(
            data_frame,
            textvariable=self.cd_var,
            font=("Arial", 18, "bold")
        ).pack(
            pady=5
        )


        # ====================================================
        # DIREITA
        # ====================================================

        right = ttk.Frame(main)


        right.pack(
            side="right",
            fill="both",
            expand=True
        )


        # ====================================================
        # GRÁFICO
        # ====================================================

        graph_frame = ttk.LabelFrame(
            right,
            text="Força × Velocidade",
            padding=5
        )


        graph_frame.pack(
            fill="both",
            expand=True
        )


        self.figure = Figure(
            figsize=(8, 5),
            dpi=100
        )


        self.ax = self.figure.add_subplot(111)


        self.ax.set_xlabel(
            "Velocidade (km/h)"
        )


        self.ax.set_ylabel(
            "Força (N)"
        )


        self.ax.set_title(
            "Aguardando início do teste"
        )


        self.ax.grid(
            True
        )


        self.canvas_plot = FigureCanvasTkAgg(
            self.figure,
            master=graph_frame
        )


        self.canvas_plot.draw()


        self.canvas_plot.get_tk_widget().pack(
            fill="both",
            expand=True
        )


    # ========================================================
    # PORTAS
    # ========================================================

    def refresh_ports(self):

        ports = [
            p.device
            for p in list_ports.comports()
        ]


        self.com_combo["values"] = ports


        if ports:

            if self.com_var.get() not in ports:

                self.com_var.set(
                    ports[0]
                )


    # ========================================================
    # CONEXÃO
    # ========================================================

    def toggle_connection(self):

        if self.ser and self.ser.is_open:

            self.disconnect()

        else:

            self.connect()


    def connect(self):

        port = self.com_var.get()


        if not port:

            messagebox.showerror(
                "Serial",
                "Selecione uma porta COM."
            )

            return


        try:

            self.ser = serial.Serial(
                port,
                BAUDRATE,
                timeout=0.1
            )


            time.sleep(2)


            self.serial_running = True


            self.serial_thread = threading.Thread(
                target=self.serial_reader,
                daemon=True
            )


            self.serial_thread.start()


            self.status_var.set(
                f"Conectado: {port}"
            )


            self.connect_button.config(
                text="Desconectar"
            )


            # Por segurança, inicia no modo PC
            self.manual_mode = False

            self.mode_var.set(
                "MODO PC"
            )

            self.manual_mode_button.config(
                text="ATIVAR MODO MANUAL"
            )


            self.send(
                "MODE,PC"
            )


            self.send(
                "PING"
            )


        except SerialException as e:

            messagebox.showerror(
                "Serial",
                str(e)
            )


    def disconnect(self):

        self.serial_running = False


        if self.ser:

            try:

                self.ser.close()

            except Exception:
                pass


        self.ser = None


        self.status_var.set(
            "Desconectado"
        )


        self.connect_button.config(
            text="Conectar"
        )


    # ========================================================
    # LEITURA SERIAL
    # ========================================================

    def serial_reader(self):

        while self.serial_running:

            try:

                if self.ser and self.ser.is_open:

                    line = (
                        self.ser.readline()
                        .decode(
                            "utf-8",
                            errors="replace"
                        )
                        .strip()
                    )


                    if line:

                        self.serial_queue.put(
                            line
                        )

                else:

                    time.sleep(0.1)


            except Exception:

                break


    # ========================================================
    # ENVIO SERIAL
    # ========================================================

    def send(self, command):

        if not self.ser or not self.ser.is_open:

            return False


        try:

            self.ser.write(
                (command + "\n").encode()
            )


            return True


        except SerialException:

            return False


    # ========================================================
    # PROCESSAMENTO SERIAL
    # ========================================================

    def process_serial_queue(self):

        try:

            while True:

                line = (
                    self.serial_queue
                    .get_nowait()
                )


                self.process_line(
                    line
                )


        except queue.Empty:

            pass


        self.root.after(
            100,
            self.process_serial_queue
        )


    # ========================================================
    # PROCESSA LINHA
    # ========================================================

    def process_line(self, line):

        parts = line.split(",")


        if not parts:

            return


        # ====================================================
        # DATA
        # ====================================================

        if parts[0] == "DATA":

            self.process_data(parts)

            return


        # ====================================================
        # TESTE INICIADO
        # ====================================================

        if (
            len(parts) >= 3
            and parts[0] == "OK"
            and parts[1] == "TEST"
            and parts[2] == "START"
        ):

            self.test_running = True

            self.acquisition_active = True

            self.test_status_var.set(
                "TESTE EM ANDAMENTO..."
            )

            return


        # ====================================================
        # TESTE FINALIZADO
        # ====================================================

        if (
            len(parts) >= 3
            and parts[0] == "OK"
            and parts[1] == "TEST"
            and parts[2] == "END"
        ):

            self.test_running = False

            if self.double_mode_var.get() and self.comparison_stage == 1:
                with self.lock:
                    self.comparison_samples[1] = list(self.samples)
                self.comparison_stage = 2
                self.test_status_var.set("2º TESTE FINALIZADO — COMPARAÇÃO PRONTA")
            else:
                self.test_status_var.set("TESTE FINALIZADO")

            return


        # ====================================================
        # TEMPERATURA DEFINIDA
        # ====================================================

        if (
            len(parts) >= 3
            and parts[0] == "OK"
            and parts[1] == "TARGET_TEMP"
        ):

            try:

                self.target_temperature = float(
                    parts[2]
                )

                self.target_temp_var.set(
                    f"{self.target_temperature:.1f}"
                )

                self.update_heater_info()

            except ValueError:

                pass

            return


        # ====================================================
        # AQUECIMENTO
        # ====================================================

        if (
            len(parts) >= 3
            and parts[0] == "OK"
            and parts[1] == "HEATER"
        ):

            try:

                self.heater_enabled = (
                    int(parts[2]) == 1
                )

                self.update_heater_info()

            except ValueError:

                pass

            return


    # ========================================================
    # PROCESSA DATA
    # ========================================================

    def process_data(self, parts):

        # DATA + 9 valores = 10 campos

        if len(parts) != 10:

            return


        try:

            sample = Sample(

                timestamp_ms=float(
                    parts[1]
                ),

                pwm=float(
                    parts[2]
                ),

                rpm=float(
                    parts[3]
                ),

                speed_kmh=float(
                    parts[4]
                ),

                force_n=float(
                    parts[5]
                ),

                temperature_c=float(
                    parts[6]
                ),

                fan=int(
                    parts[7]
                ),

                led=int(
                    parts[8]
                ),

                smoke=int(
                    parts[9]
                )
            )


        except ValueError:

            return


        # ====================================================
        # VALORES ATUAIS
        # ====================================================
        #
        # Os valores de temperatura, RPM, velocidade e força
        # continuam sendo atualizados sempre.
        #
        # ====================================================

        with self.lock:
            self.latest_sample = sample


        # ====================================================
        # DADOS DO GRÁFICO
        # ====================================================
        #
        # Somente um TESTE automático registra pontos no gráfico.
        #
        # ====================================================

        if self.test_running:

            with self.lock:

                self.samples.append(
                    sample
                )

                if len(self.samples) > 10000:

                    self.samples.pop(0)


    # ========================================================
    # ATUALIZA INTERFACE
    # ========================================================

    def update_interface(self):

        with self.lock:

            s = self.latest_sample


        if s:

            self.speed_var.set(
                f"{s.speed_kmh:.1f} km/h"
            )


            self.force_var.set(
                f"{s.force_n:.3f} N"
            )


            self.rpm_var.set(
                f"{s.rpm:.0f} RPM"
            )


            self.temp_var.set(
                f"{s.temperature_c:.1f} °C"
            )


            self.motor_var.set(
                "LIGADO"
                if s.fan
                else
                "DESLIGADO"
            )


            self.led_var.set(
                "LIGADA"
                if s.led
                else
                "DESLIGADA"
            )


            self.smoke_var.set(
                "ATIVA"
                if s.smoke
                else
                "OFF"
            )


            # =================================================
            # TEMPERATURA
            # =================================================

            if s.temperature_c > MAX_TEMP_C:

                self.temp_label.config(
                    style="TempHigh.TLabel"
                )


                self.temp_alert_label.config(
                    text="!! TEMPERATURA ELEVADA !!"
                )

            else:

                self.temp_label.config(
                    style="TempNormal.TLabel"
                )


                self.temp_alert_label.config(
                    text=""
                )


        # ====================================================
        # GRÁFICO
        # ====================================================

        if self.test_running:

            self.update_graph()


        self.update_heater_info()


        self.root.after(
            250,
            self.update_interface
        )


    # ========================================================
    # GRÁFICO
    # ========================================================

    def update_graph(self):
        with self.lock:
            data = list(self.samples)
            first_data = list(self.comparison_samples[0])
            second_data = list(self.comparison_samples[1])

        self.ax.clear()
        double_mode = self.double_mode_var.get() and self.comparison_stage is not None

        if double_mode:
            if self.comparison_stage in (1, 2) and first_data:
                self.ax.plot(
                    [s.speed_kmh for s in first_data],
                    [s.force_n for s in first_data],
                    color="blue", marker=".", linestyle="-",
                    label=self.object_name_var.get().strip() or "Carrinho X"
                )
            current_second = second_data if self.comparison_stage == 2 else data
            if self.comparison_stage in (1, 2) and current_second:
                self.ax.plot(
                    [s.speed_kmh for s in current_second],
                    [s.force_n for s in current_second],
                    color="red", marker=".", linestyle="-",
                    label=self.object2_name_var.get().strip() or "Carrinho Y"
                )
            if self.comparison_stage == 0 and data:
                self.ax.plot(
                    [s.speed_kmh for s in data], [s.force_n for s in data],
                    color="blue", marker=".", linestyle="-",
                    label=self.object_name_var.get().strip() or "Carrinho X"
                )
            if self.comparison_stage in (1, 2):
                self.ax.legend(loc="best")
        elif data:
            self.ax.plot([s.speed_kmh for s in data], [s.force_n for s in data], marker=".", linestyle="-")

        self.ax.set_xlim(0, GRAPH_MAX_SPEED_KMH)
        self.ax.set_ylim(0, GRAPH_MAX_FORCE_N)
        self.ax.set_xlabel("Velocidade (km/h)")
        self.ax.set_ylabel("Força (N)")
        if self.test_running:
            self.ax.set_title("TESTE EM ANDAMENTO..." if not double_mode else "COMPARAÇÃO — TESTE EM ANDAMENTO")
        else:
            self.ax.set_title("Comparação: Força × Velocidade" if double_mode and self.comparison_stage in (1, 2) else "Força × Velocidade")
        self.ax.grid(True)
        self.canvas_plot.draw_idle()


    # ========================================================
    # MODO MANUAL / PC
    # ========================================================

    def toggle_manual_mode(self):

        if not self._is_connected():

            return


        if self.manual_mode:

            # -----------------------------------------------
            # VOLTAR PARA PC
            # -----------------------------------------------

            self.manual_mode = False


            self.mode_var.set(
                "MODO PC"
            )


            self.manual_mode_button.config(
                text="ATIVAR MODO MANUAL"
            )


            # Garante que o computador retome o PWM atual
            pwm = int(
                self.pwm_scale.get()
            )


            self.send(
                "MODE,PC"
            )


            self.send(
                f"SETPWM,{pwm}"
            )


        else:

            # -----------------------------------------------
            # ENTRAR NO MODO MANUAL
            # -----------------------------------------------

            self.manual_mode = True


            self.mode_var.set(
                "MODO MANUAL"
            )


            self.manual_mode_button.config(
                text="VOLTAR PARA MODO PC"
            )


            self.send(
                "MODE,MANUAL"
            )


    # ========================================================
    # PWM
    # ========================================================

    def on_pwm_slider(self, value):

        pwm = int(
            float(value)
        )


        self.pwm_var.set(
            f"PWM: {pwm}"
        )


        # ====================================================
        # SOMENTE MODO PC ENVIA PWM
        # ====================================================

        if self.manual_mode:

            return


        if not self.ser or not self.ser.is_open:

            return


        self.send(
            f"SETPWM,{pwm}"
        )


    # ========================================================
    # START FAN
    # ========================================================

    def start_fan(self):

        if not self._is_connected():

            return


        if self.manual_mode:

            messagebox.showinfo(
                "Modo manual",
                "O FAN está em modo manual.\n\n"
                "Use o potenciômetro para controlar "
                "a velocidade."
            )

            return


        pwm = int(
            self.pwm_scale.get()
        )


        if pwm <= 0:

            pwm = 1

            self.pwm_scale.set(
                pwm
            )


        self.send(
            f"SETPWM,{pwm}"
        )


        self.send(
            "START"
        )


        self.acquisition_active = True


        self.test_status_var.set(
            "TESTE EM ANDAMENTO..."
        )


    # ========================================================
    # STOP FAN
    # ========================================================

    def stop_fan(self):

        if not self._is_connected():

            return


        self.send(
            "STOP"
        )


        self.test_running = False

        self.acquisition_active = False


        self.test_status_var.set(
            "TESTE FINALIZADO"
        )


    # ========================================================
    # STOP TESTE
    # ========================================================

    def stop_test(self):

        if not self._is_connected():

            return


        self.send(
            "STOP"
        )


        self.test_running = False

        self.acquisition_active = False


        self.test_status_var.set(
            "TESTE FINALIZADO"
        )


    # ========================================================
    # LUZ
    # ========================================================

    def set_led(self, on):

        if not self._is_connected():

            return


        self.send(
            f"LED,{'ON' if on else 'OFF'}"
        )


    # ========================================================
    # FUMAÇA
    # ========================================================

    def smoke_pulse(self):

        if not self._is_connected():

            return


        try:

            duration = int(
                self.smoke_duration_var.get()
            )


            if not (
                MIN_SMOKE_MS
                <= duration
                <= MAX_SMOKE_MS
            ):

                raise ValueError


        except ValueError:

            messagebox.showerror(
                "Fumaça",
                "Informe um pulso entre "
                "50 e 5000 ms."
            )

            return


        self.send(
            f"SMOKE,{duration}"
        )


    # ========================================================
    # TEMPERATURA
    # ========================================================

    def set_temperature(self):

        if not self._is_connected():

            return


        try:

            temp = float(
                self.target_temp_var.get()
            )


            if not (
                0.0
                <= temp
                <= 250.0
            ):

                raise ValueError


        except ValueError:

            messagebox.showerror(
                "Temperatura",
                "A temperatura alvo deve "
                "estar entre 0 e 250 °C."
            )

            return


        self.target_temperature = temp


        # Envia para Arduino.
        # O Arduino deverá emitir o bip
        # ao receber SETTEMP.

        self.send(
            f"SETTEMP,{temp:.1f}"
        )


        self.update_heater_info()


    # ========================================================
    # LIGAR AQUECIMENTO
    # ========================================================

    def enable_heater(self):

        if not self._is_connected():

            return


        # Primeiro envia a temperatura alvo
        self.set_temperature()


        self.heater_enabled = True


        self.send(
            "HEATER,ON"
        )


        self.update_heater_info()


    # ========================================================
    # DESLIGAR AQUECIMENTO
    # ========================================================

    def disable_heater(self):

        if not self._is_connected():

            return


        self.heater_enabled = True


        self.send(
            "HEATER,OFF"
        )


        self.update_heater_info()


    # ========================================================
    # BLOCO DE AQUECIMENTO
    # ========================================================

    def update_heater_info(self):

        status = (
            "LIGADO"
            if self.heater_enabled
            else
            "DESLIGADO"
        )


        self.heater_status_var.set(
            status
        )


        self.heater_info_var.set(
            f"Alvo: {self.target_temperature:.1f} °C"
            f" | Aquecimento: {status}"
        )


    # ========================================================
    # TESTE AUTOMÁTICO
    # ========================================================

    def start_test(self):

        if not self._is_connected():

            return


        if self.manual_mode:

            messagebox.showwarning(
                "Modo manual",
                "Desative o modo manual antes "
                "de iniciar o teste automático."
            )

            return


        try:

            duration = int(
                self.test_duration_var.get()
            )


            pwm_start = int(
                self.test_start_pwm_var.get()
            )


            pwm_end = int(
                self.test_end_pwm_var.get()
            )


            if duration < 1000:

                raise ValueError


            if not (
                0
                <= pwm_start
                <= 255
            ):

                raise ValueError


            if not (
                0
                <= pwm_end
                <= 255
            ):

                raise ValueError


        except ValueError:

            messagebox.showerror(
                "Teste",
                "Valores inválidos."
            )

            return


        # ====================================================
        # PREPARA MODO NORMAL OU COMPARAÇÃO DUPLA
        # ====================================================
        if self.double_mode_var.get():
            try:
                area1 = float(self.area_var.get())
                area2 = float(self.area2_var.get())
                if area1 <= 0 or area2 <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Comparação dupla", "Informe áreas frontais positivas para os dois carrinhos.")
                return

            if self.test_running:
                messagebox.showwarning("Teste", "Aguarde o teste atual terminar antes de iniciar o próximo carrinho.")
                return

            if self.comparison_stage is None or self.comparison_stage == 2:
                # Começa uma nova comparação com o carrinho X.
                self.comparison_samples = [[], []]
                self.comparison_stage = 0
                with self.lock:
                    self.samples.clear()
            elif self.comparison_stage == 0:
                # O primeiro teste terminou: guarda seus dados e prepara o carrinho Y.
                with self.lock:
                    self.comparison_samples[0] = list(self.samples)
                    self.samples.clear()
                self.comparison_stage = 1
            else:
                messagebox.showwarning("Comparação dupla", "Finalize o teste do primeiro carrinho antes de iniciar o segundo.")
                return
        else:
            self.comparison_stage = None
            self.comparison_samples = [[], []]
            with self.lock:
                self.samples.clear()

        # ====================================================
        # INICIA AQUISIÇÃO
        # ====================================================

        self.test_running = True

        self.acquisition_active = True


        if self.double_mode_var.get():
            if self.comparison_stage == 0:
                self.test_status_var.set("1º TESTE — " + (self.object_name_var.get().strip() or "Carrinho X"))
            else:
                self.test_status_var.set("2º TESTE — " + (self.object2_name_var.get().strip() or "Carrinho Y"))
        else:
            self.test_status_var.set("TESTE EM ANDAMENTO...")

        # No modo duplo, mantém a primeira curva visível ao iniciar a segunda.
        if not (self.double_mode_var.get() and self.comparison_stage == 1):
            self.clear_graph_only()


        self.send(
            f"TEST,{duration},{pwm_start},{pwm_end}"
        )


    # ========================================================
    # TARA
    # ========================================================

    def tare(self):

        if not self._is_connected():

            return


        self.send(
            "TARE"
        )


    # ========================================================
    # LIMPAR DADOS
    # ========================================================

    def clear_data(self):

        with self.lock:
            self.samples.clear()
            self.comparison_samples = [[], []]

        self.comparison_stage = None
        self.cd_var.set(
            "--"
        )


        self.acquisition_active = False


        self.clear_graph_only()


    # ========================================================
    # LIMPA SOMENTE GRÁFICO
    # ========================================================

    def clear_graph_only(self):

        self.ax.clear()


        self.ax.set_xlabel(
            "Velocidade (km/h)"
        )


        self.ax.set_ylabel(
            "Força (N)"
        )


        self.ax.set_title(
            "Aguardando início do teste"
        )

        self.ax.set_xlim(0, GRAPH_MAX_SPEED_KMH)
        self.ax.set_ylim(0, GRAPH_MAX_FORCE_N)

        self.ax.grid(
            True
        )


        self.canvas_plot.draw_idle()


    # ========================================================
    # SALVAR GRÁFICO
    # ========================================================

    def save_graph(self):

        with self.lock:
            data = list(self.samples)
            comparison_data = [list(items) for items in self.comparison_samples]

        double_mode = self.double_mode_var.get() and self.comparison_stage in (1, 2)
        if double_mode:
            if self.comparison_stage == 1:
                comparison_data[1] = data
            if not comparison_data[0] or not comparison_data[1]:
                messagebox.showwarning("Gráfico", "Para salvar a comparação, conclua os testes dos dois carrinhos.")
                return
        elif not data:
            messagebox.showwarning("Gráfico", "Não há dados para salvar.")
            return

        # --------------------------------------------------------
        # Metadados e gráfico (modo simples ou comparação dupla)
        # --------------------------------------------------------
        object_name = self.object_name_var.get().strip() or "Carrinho X"
        try:
            area_m2 = float(self.area_var.get())
            density = float(self.density_var.get())
            area2_m2 = float(self.area2_var.get()) if double_mode else area_m2
        except ValueError:
            messagebox.showerror("Gráfico", "Área ou densidade inválida.")
            return
        if area_m2 <= 0 or area2_m2 <= 0 or density <= 0:
            messagebox.showerror("Gráfico", "Áreas e densidade devem ser positivas.")
            return

        figure = Figure(figsize=(8, 5), dpi=100)
        ax = figure.add_subplot(111)

        def cd_medio(samples, area):
            return compute_cd(samples, density, area)

        def plot_series(samples, name, color, area):
            if not samples:
                return
            peak = max(samples, key=lambda sample: abs(sample.force_n))
            cd_value = cd_medio(samples, area)
            cd_label = f"{cd_value:.4f}" if cd_value is not None else "--"
            ax.plot(
                [sample.speed_kmh for sample in samples],
                [sample.force_n for sample in samples],
                color=color, marker=".", linestyle="-",
                label=f"{name} | Área: {area:.4f} m² | pico: {abs(peak.force_n):.3f} N a {peak.speed_kmh:.1f} km/h | Cd médio: {cd_label}"
            )
            return cd_label

        if double_mode:
            name2 = self.object2_name_var.get().strip() or "Carrinho Y"
            cd1 = plot_series(comparison_data[0], object_name, "blue", area_m2)
            cd2 = plot_series(comparison_data[1], name2, "red", area2_m2)
            ax.set_title("Comparação de arrasto: " + object_name + " × " + name2, fontsize=13)
            ax.legend(loc="best", fontsize=8)
            self.cd_var.set(f"Cd X: {cd1 or '--'} | Cd Y: {cd2 or '--'}")
        else:
            peak = max(data, key=lambda sample: abs(sample.force_n))
            cd_value = cd_medio(data, area_m2)
            cd_text = f"{cd_value:.4f}" if cd_value is not None else "--"
            plot_series(data, object_name, "blue", area_m2)
            ax.set_title(object_name, fontsize=14)
            ax.legend(handles=[
                ax.lines[0],
                Line2D([], [], linestyle="None", label=f"Área frontal: {area_m2:.4f} m²"),
                Line2D([], [], linestyle="None", label=f"Densidade do ar: {density:.3f} kg/m³"),
                Line2D([], [], linestyle="None", label=f"Coeficiente de arrasto (Cd): {cd_text}")
            ], loc="best")
            self.cd_var.set(cd_text)

        ax.set_xlabel("Velocidade (km/h)")
        ax.set_ylabel("Força (N)")
        ax.set_xlim(0, GRAPH_MAX_SPEED_KMH)
        ax.set_ylim(0, GRAPH_MAX_FORCE_N)
        ax.grid(True)
        figure.tight_layout()

        filename = filedialog.asksaveasfilename(
            title="Salvar gráfico",
            defaultextension=".png",
            filetypes=[
                ("PNG", "*.png"),
                ("JPEG", "*.jpg"),
                ("JPEG", "*.jpeg")
            ]
        )

        if not filename:
            return

        try:
            figure.savefig(filename, dpi=150, bbox_inches="tight")
            messagebox.showinfo(
                "Gráfico",
                "Gráfico salvo com sucesso."
            )
        except Exception as e:
            messagebox.showerror(
                "Gráfico",
                str(e)
            )
        finally:
            import matplotlib.pyplot as plt
            plt.close(figure)


    # ========================================================
    # CALCULAR Cd
    # ========================================================

    def calculate_cd(self):

        try:
            area_m2 = float(self.area_var.get())
            density = float(self.density_var.get())
        except ValueError:
            messagebox.showerror(
                "Cd",
                "Área ou densidade inválida."
            )
            return

        if area_m2 <= 0 or density <= 0:
            messagebox.showerror(
                "Cd",
                "Área e densidade devem ser positivas."
            )
            return

        with self.lock:
            data = list(self.samples)

        if not data:
            messagebox.showwarning(
                "Cd",
                "Não há dados."
            )
            return

        cd_value = compute_cd(data, density, area_m2)

        if cd_value is None:
            self.cd_var.set("--")
            return

        self.cd_var.set(f"{cd_value:.4f}")


    # ========================================================
    # VERIFICAÇÃO DE CONEXÃO
    # ========================================================

    def _is_connected(self):

        if not self.ser or not self.ser.is_open:

            messagebox.showwarning(
                "Serial",
                "Arduino não está conectado."
            )

            return False


        return True


    # ========================================================
    # FECHAR
    # ========================================================

    def on_close(self):

        try:

            if self.ser and self.ser.is_open:

                self.send(
                    "STOP"
                )


                self.send(
                    "LED,OFF"
                )


                self.send(
                    "HEATER,OFF"
                )


                self.send(
                    "MODE,PC"
                )


                time.sleep(0.1)


                self.ser.close()


        except Exception:

            pass


        self.serial_running = False


        self.root.destroy()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    root = tk.Tk()


    app = WindTunnelApp(
        root
    )


    root.mainloop()