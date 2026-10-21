/*
============================================================
WIND TUNNEL 2.0 - ARDUINO UNO
============================================================

TELA OLED:
  Velocidade km/h
  Forca N
  Temperatura C

SENSORES:
  POT        A0
  NTC 100K   A1
  HALL       D2
  HX711 DT   D4
  HX711 SCK  D5

CONTROLES LOCAIS:
  D7 = Botao luz externa
  D6 = Botao bomba de fumaça
  A0 = Potenciometro FAN

RELES:
  D9  = LUZ EXTERNA - ATIVO HIGH
  D3  = BOMBA FUMACA - ATIVO LOW
  D11 = AQUECIMENTO - ATIVO LOW

OUTROS:
  D8  = Buzzer
  D10 = FAN PWM
  D13 = LED AQUECIMENTO


SERIAL:
  115200 baud

FORMATO DATA:
  DATA,time_ms,pwm,rpm,speed_kmh,force_n,temp_c,fan,led,smoke

COMANDOS:
  PING
  START
  STOP
  SETPWM,0..255
  TARE

  LED,ON
  LED,OFF
  LED,1
  LED,0

  SMOKE
  SMOKE,300

  SETTEMP,0..250
  HEATER,ON
  HEATER,OFF

  TEST,duracao_ms,pwm_inicial,pwm_final
============================================================
*/

#include <Arduino.h>
#include <Wire.h>
#include <U8g2lib.h>
#include <HX711.h>
#include <thermistor.h>
#include <math.h>

// ============================================================
// PINOS
// ============================================================

const uint8_t PIN_POT          = A0;
const uint8_t PIN_NTC          = A1;

const uint8_t PIN_HALL         = 2;

const uint8_t PIN_RELAY_SMOKE  = 3;

const uint8_t PIN_HX_DT        = 4;
const uint8_t PIN_HX_SCK       = 5;

const uint8_t PIN_BUTTON_SMOKE = 6;
const uint8_t PIN_BUTTON_LED   = 7;

const uint8_t PIN_BUZZER       = 8;

const uint8_t PIN_RELAY_LED    = 9;

const uint8_t PIN_FAN_PWM      = 10;

const uint8_t PIN_RELAY_HEATER = 11;

const uint8_t PIN_LED_HEATER   = 13;


// ============================================================
// OLED
// ============================================================

U8G2_SSD1306_128X64_NONAME_1_HW_I2C display(U8G2_R0);


// ============================================================
// HX711
// ============================================================

HX711 scale;

const float HX711_CALIBRATION = 4022.068847f;
const float FORCE_SIGN = -1.0f;

float tareGrams = 0.0f;
float forceGrams = 0.0f;
float forceN = 0.0f;


// ============================================================
// NTC
// ============================================================

THERMISTOR thermistor(
  PIN_NTC,
  100000,
  3950,
  100000
);

float temperatureC = 0.0f;


// ============================================================
// RELES
// ============================================================

// Luz externa: ATIVO HIGH
const uint8_t RELAY_LED_ON  = HIGH;
const uint8_t RELAY_LED_OFF = LOW;

// Fumaca: ATIVO LOW
const uint8_t RELAY_SMOKE_ON  = LOW;
const uint8_t RELAY_SMOKE_OFF = HIGH;

// Aquecimento: ATIVO LOW
const uint8_t RELAY_HEATER_ON  = LOW;
const uint8_t RELAY_HEATER_OFF = HIGH;


// ============================================================
// ESTADOS
// ============================================================

bool fanEnabled = false;

bool tunnelLedOn = false;

bool smokeActive = false;

bool heaterEnabled = true;

float targetTemperatureC = 200.0f;


// ============================================================
// FAN / PWM
// ============================================================

int fanPWM = 0;

uint16_t lastPot1000 = 0;

const uint16_t POT_CHANGE_THRESHOLD = 8;


// ============================================================
// HALL / RPM
// ============================================================

volatile unsigned long contadorPulsos = 0;

const uint8_t PULSOS_POR_ROTACAO = 2;

float rpm = 0.0f;
float rpmFiltrado = 0.0f;

const float RPM_ALPHA = 0.35f;


// ============================================================
// VELOCIDADE
// ============================================================

float speedKmh = 0.0f;

/*
  Fator original preservado.
*/
const float SPEED_FACTOR = 20.0f / 1000.0f;


// ============================================================
// TEMPOS
// ============================================================

const unsigned long DISPLAY_INTERVAL = 180;
const unsigned long DATA_INTERVAL    = 250;
const unsigned long TEMP_INTERVAL    = 500;
const unsigned long FORCE_INTERVAL   = 100;
const unsigned long RPM_INTERVAL     = 500;

const unsigned long BUTTON_DEBOUNCE = 180;

const unsigned long DEFAULT_SMOKE_PULSE_MS = 300;


// ============================================================
// CONTROLE DE TEMPO
// ============================================================

unsigned long lastDisplayMs = 0;
unsigned long lastDataMs = 0;
unsigned long lastTempMs = 0;
unsigned long lastForceMs = 0;
unsigned long lastRpmMs = 0;

unsigned long lastLedButtonMs = 0;
unsigned long lastSmokeButtonMs = 0;


// ============================================================

// ============================================================
// FUMACA
// ============================================================

unsigned long smokePulseEndMs = 0;


// ============================================================
// TESTE AUTOMATICO
// ============================================================

bool automaticTest = false;

unsigned long testStartMs = 0;
unsigned long testDurationMs = 0;

int testPWMStart = 0;
int testPWMEnd = 255;


// ============================================================
// SERIAL
// ============================================================

String serialBuffer;


// ============================================================
// INTERRUPCAO HALL
// ============================================================

void pulsoHall() {
  contadorPulsos++;
}


// ============================================================
// BEEP
// ============================================================

void beep(unsigned int frequency = 2200, unsigned int duration = 70) {
  tone(PIN_BUZZER, frequency, duration);
}


// ============================================================
// POTENCIOMETRO 0..1000
// ============================================================

uint16_t readPot1000() {

  int raw = analogRead(PIN_POT);

  raw = constrain(raw, 0, 1023);

  return (uint16_t)((long)raw * 1000L / 1023L);
}


// ============================================================
// FAN PWM
// ============================================================

void setFanPwm(int pwm) {

  pwm = constrain(pwm, 0, 255);

  fanPWM = pwm;

  analogWrite(PIN_FAN_PWM, fanPWM);

  if (fanPWM > 0) {
    fanEnabled = true;
  } else {
    fanEnabled = false;
  }
}


// ============================================================
// FAN START
// ============================================================

void startFan() {

  if (fanPWM <= 0) {
    fanPWM = 1;
  }

  analogWrite(PIN_FAN_PWM, fanPWM);

  fanEnabled = true;

  beep();
}


// ============================================================
// FAN STOP
// ============================================================

void stopFan() {

  analogWrite(PIN_FAN_PWM, 0);

  fanPWM = 0;

  fanEnabled = false;

  automaticTest = false;

  beep(1800, 100);
}


// ============================================================
// LUZ EXTERNA
// ============================================================

void setTunnelLed(bool state) {

  tunnelLedOn = state;

  digitalWrite(
    PIN_RELAY_LED,
    tunnelLedOn ? RELAY_LED_ON : RELAY_LED_OFF
  );
}


// ============================================================
// FUMACA
// ============================================================

void triggerSmokePulse(unsigned long durationMs = DEFAULT_SMOKE_PULSE_MS) {

  durationMs = constrain(durationMs, 50UL, 5000UL);

  digitalWrite(PIN_RELAY_SMOKE, RELAY_SMOKE_ON);

  smokeActive = true;

  smokePulseEndMs = millis() + durationMs;

  beep(2500, 80);
}


void updateSmokePulse() {

  if (!smokeActive) {
    return;
  }

  if ((long)(millis() - smokePulseEndMs) >= 0) {

    digitalWrite(PIN_RELAY_SMOKE, RELAY_SMOKE_OFF);

    smokeActive = false;
  }
}


// ============================================================
// AQUECIMENTO / TERMOSTATO
// ============================================================

void updateHeater() {

  /*
    Se o aquecimento estiver desabilitado,
    garante rele desligado.
  */

  if (!heaterEnabled) {

    digitalWrite(
      PIN_RELAY_HEATER,
      RELAY_HEATER_OFF
    );

    return;
  }


  /*
    Protecao absoluta acima de 250 C.
  */

  if (temperatureC >= 250.0f) {

    digitalWrite(
      PIN_RELAY_HEATER,
      RELAY_HEATER_OFF
    );

    return;
  }


  /*
    Termostato:
      abaixo do alvo -> aquece
      igual/acima -> desliga
  */

  if (temperatureC < targetTemperatureC) {

    digitalWrite(
      PIN_RELAY_HEATER,
      RELAY_HEATER_ON
    );

  } else {

    digitalWrite(
      PIN_RELAY_HEATER,
      RELAY_HEATER_OFF
    );
  }
}


// ============================================================
// TEMPERATURA
// ============================================================

void updateTemperature() {

  if (millis() - lastTempMs < TEMP_INTERVAL) {
    return;
  }

  lastTempMs = millis();

  int rawTemperature = thermistor.read();

  temperatureC = (float)rawTemperature / 10.0f;

  updateHeater();
}


// ============================================================
// FORCA
// ============================================================

void updateForce() {

  if (millis() - lastForceMs < FORCE_INTERVAL) {
    return;
  }

  lastForceMs = millis();

  if (!scale.is_ready()) {
    return;
  }

  float grams = scale.get_units(1);

  if (fabs(grams) < 0.5f) {
    grams = 0.0f;
  }

  forceGrams = grams;

  forceN =
    FORCE_SIGN *
    forceGrams *
    0.00980665f;
}


// ============================================================
// TARA
// ============================================================

void doTare() {

  if (!scale.is_ready()) {

    Serial.println("ERROR,HX711_NOT_READY");

    return;
  }

  float measured = scale.get_units(10);

  tareGrams += measured;

  scale.tare(10);

  forceGrams = 0.0f;
  forceN = 0.0f;

  Serial.print("OK,TARE_G,");
  Serial.println(tareGrams, 3);

  beep(2000, 100);
}


// ============================================================
// RPM
// ============================================================

void updateRPM() {

  if (millis() - lastRpmMs < RPM_INTERVAL) {
    return;
  }

  unsigned long now = millis();

  unsigned long elapsed =
    now - lastRpmMs;

  lastRpmMs = now;


  noInterrupts();

  unsigned long pulses =
    contadorPulsos;

  contadorPulsos = 0;

  interrupts();


  if (elapsed == 0) {
    return;
  }


  /*
    RPM =
    pulsos / pulsos_por_rotacao
    / tempo_em_minutos
  */

  float revolutions =
    (float)pulses /
    (float)PULSOS_POR_ROTACAO;

  float rpmInstant =
    revolutions *
    60000.0f /
    (float)elapsed;


  if (rpmInstant < 0.0f) {
    rpmInstant = 0.0f;
  }


  rpm =
    rpmInstant;


  if (rpmFiltrado == 0.0f) {

    rpmFiltrado = rpm;

  } else {

    rpmFiltrado =
      RPM_ALPHA * rpm +
      (1.0f - RPM_ALPHA) * rpmFiltrado;
  }


  /*
    Se fan desligado, RPM deve voltar a zero.
  */

  if (!fanEnabled) {

    rpmFiltrado *= 0.7f;

    if (rpmFiltrado < 5.0f) {
      rpmFiltrado = 0.0f;
    }
  }


  rpm = rpmFiltrado;


  /*
    Conversao original
  */

  speedKmh =
    rpm *
    SPEED_FACTOR;
}


// ============================================================
// LED DO AQUECIMENTO
// ============================================================

void updateHeaterLED() {

  // D13 acompanha exclusivamente o estado REAL do rele
  // de aquecimento (ativo LOW).
  bool heaterRelayOn =
    digitalRead(PIN_RELAY_HEATER) == RELAY_HEATER_ON;

  digitalWrite(
    PIN_LED_HEATER,
    heaterRelayOn ? HIGH : LOW
  );
}


// ============================================================
// POT LOCAL
// ============================================================

void updateLocalPot() {

  uint16_t pot = readPot1000();

  /*
    O potenciometro so assume o controle
    quando houver movimento real.
  */

  if (
    abs(
      (int)pot -
      (int)lastPot1000
    ) >= POT_CHANGE_THRESHOLD
  ) {

    lastPot1000 = pot;

    int pwm =
      (long)pot *
      255L /
      1000L;

    setFanPwm(pwm);
  }
}


// ============================================================
// BOTAO
// ============================================================

bool buttonPressed(
  uint8_t pin,
  unsigned long &lastPress
) {

  if (digitalRead(pin) != LOW) {
    return false;
  }

  if (millis() - lastPress < BUTTON_DEBOUNCE) {
    return false;
  }

  lastPress = millis();

  while (digitalRead(pin) == LOW) {
    delay(2);
  }

  return true;
}


// ============================================================
// BOTOES LOCAIS
// ============================================================

void updateButtons() {

  // -------------------------------
  // BOTAO LUZ
  // -------------------------------

  if (
    buttonPressed(
      PIN_BUTTON_LED,
      lastLedButtonMs
    )
  ) {

    setTunnelLed(!tunnelLedOn);

    beep();
  }


  // -------------------------------
  // BOTAO FUMACA
  // -------------------------------

  if (
    buttonPressed(
      PIN_BUTTON_SMOKE,
      lastSmokeButtonMs
    )
  ) {

    triggerSmokePulse();
  }
}


// ============================================================
// TESTE AUTOMATICO
// ============================================================

void startAutomaticTest(
  unsigned long durationMs,
  int pwmStart,
  int pwmEnd
) {

  durationMs =
    constrain(
      durationMs,
      1000UL,
      3600000UL
    );

  pwmStart =
    constrain(
      pwmStart,
      0,
      255
    );

  pwmEnd =
    constrain(
      pwmEnd,
      0,
      255
    );

  automaticTest = true;

  testStartMs = millis();

  testDurationMs = durationMs;

  testPWMStart = pwmStart;

  testPWMEnd = pwmEnd;

  setFanPwm(testPWMStart);

  fanEnabled = true;

  Serial.println("OK,TEST,START");

  beep(2300, 100);
}


// ============================================================
// ATUALIZA TESTE
// ============================================================

void updateAutomaticTest() {

  if (!automaticTest) {
    return;
  }

  unsigned long elapsed =
    millis() - testStartMs;


  if (elapsed >= testDurationMs) {

    automaticTest = false;

    stopFan();

    Serial.println("OK,TEST,END");

    return;
  }


  float progress =
    (float)elapsed /
    (float)testDurationMs;


  int currentPWM =
    testPWMStart +
    (
      (testPWMEnd - testPWMStart) *
      progress
    );


  currentPWM =
    constrain(
      currentPWM,
      0,
      255
    );


  fanPWM = currentPWM;

  analogWrite(
    PIN_FAN_PWM,
    fanPWM
  );

  fanEnabled =
    fanPWM > 0;
}


// ============================================================
// OLED
// ============================================================

void drawMainScreen() {

  char speedText[24];
  char forceText[24];
  char tempText[24];


  dtostrf(
    speedKmh,
    0,
    1,
    speedText
  );

  dtostrf(
    forceN,
    0,
    3,
    forceText
  );

  dtostrf(
    temperatureC,
    0,
    1,
    tempText
  );


  char line1[32];
  char line2[32];
  char line3[32];


  snprintf(
    line1,
    sizeof(line1),
    "%s km/h",
    speedText
  );

  snprintf(
    line2,
    sizeof(line2),
    "%s N",
    forceText
  );

  snprintf(
    line3,
    sizeof(line3),
    "%s C",
    tempText
  );


  display.firstPage();

  do {

    display.setFont(
      u8g2_font_10x20_tf
    );


    // -------------------------------
    // VELOCIDADE
    // -------------------------------

    display.drawStr(
      0,
      20,
      line1
    );


    // -------------------------------
    // FORCA
    // -------------------------------

    display.drawStr(
      0,
      42,
      line2
    );


    // -------------------------------
    // TEMPERATURA
    // -------------------------------

    display.drawStr(
      0,
      64,
      line3
    );

  } while (
    display.nextPage()
  );
}


// ============================================================
// TELA INICIAL
// ============================================================

void startupSequence() {

  display.firstPage();

  do {

    display.setFont(
      u8g2_font_10x20_tf
    );

    display.drawStr(
      12,
      25,
      "WIND TUNNEL"
    );

    display.drawStr(
      45,
      52,
      "2.0"
    );

  } while (
    display.nextPage()
  );


  beep(2200, 100);

  delay(700);


  display.clearBuffer();

  display.sendBuffer();
}


// ============================================================
// SERIAL DATA
// ============================================================

void sendData() {

  Serial.print("DATA,");

  Serial.print(millis());

  Serial.print(",");

  Serial.print(fanPWM);

  Serial.print(",");

  Serial.print(rpm, 1);

  Serial.print(",");

  Serial.print(speedKmh, 2);

  Serial.print(",");

  Serial.print(forceN, 4);

  Serial.print(",");

  Serial.print(temperatureC, 2);

  Serial.print(",");

  Serial.print(fanEnabled ? 1 : 0);

  Serial.print(",");

  Serial.print(tunnelLedOn ? 1 : 0);

  Serial.print(",");

  Serial.println(smokeActive ? 1 : 0);
}


// ============================================================
// SERIAL COMANDOS
// ============================================================

void processCommand(String cmd) {

  cmd.trim();

  cmd.toUpperCase();


  if (cmd.length() == 0) {
    return;
  }


  // ==========================================================
  // PING
  // ==========================================================

  if (cmd == "PING") {

    Serial.println("PONG");

    return;
  }


  // ==========================================================
  // START
  // ==========================================================

  if (cmd == "START") {

    startFan();

    Serial.print("OK,START,PWM,");
    Serial.println(fanPWM);

    return;
  }


  // ==========================================================
  // STOP
  // ==========================================================

  if (cmd == "STOP") {

    stopFan();

    Serial.println("OK,STOP");

    return;
  }


  // ==========================================================
  // TARE
  // ==========================================================

  if (cmd == "TARE") {

    doTare();

    return;
  }


  // ==========================================================
  // LED ON/OFF
  // ==========================================================

  if (
    cmd == "LED,ON" ||
    cmd == "LED,1"
  ) {

    setTunnelLed(true);

    beep();

    Serial.println("OK,LED,1");

    return;
  }


  if (
    cmd == "LED,OFF" ||
    cmd == "LED,0"
  ) {

    setTunnelLed(false);

    beep();

    Serial.println("OK,LED,0");

    return;
  }


  // ==========================================================
  // SMOKE
  // ==========================================================

  if (cmd == "SMOKE") {

    triggerSmokePulse();

    Serial.print("OK,SMOKE,");
    Serial.println(DEFAULT_SMOKE_PULSE_MS);

    return;
  }


  if (cmd.startsWith("SMOKE,")) {

    String value =
      cmd.substring(6);

    unsigned long duration =
      value.toInt();

    duration =
      constrain(
        duration,
        50UL,
        5000UL
      );

    triggerSmokePulse(duration);

    Serial.print("OK,SMOKE,");
    Serial.println(duration);

    return;
  }


  // ==========================================================
  // SET PWM
  // ==========================================================

  if (cmd.startsWith("SETPWM,")) {

    String value =
      cmd.substring(7);

    int pwm =
      value.toInt();

    pwm =
      constrain(
        pwm,
        0,
        255
      );

    setFanPwm(pwm);

    Serial.print("OK,PWM,");
    Serial.println(pwm);

    return;
  }


  // ==========================================================
  // SET TEMPERATURA ALVO
  // ==========================================================

  if (cmd.startsWith("SETTEMP,")) {

    String value =
      cmd.substring(8);

    float target =
      value.toFloat();

    target =
      constrain(
        target,
        0.0f,
        250.0f
      );

    targetTemperatureC =
      target;

    updateHeater();

    Serial.print("OK,TARGET_TEMP,");
    Serial.println(targetTemperatureC, 1);

    return;
  }


  // ==========================================================
  // HEATER ON
  // ==========================================================

  if (cmd == "HEATER,ON") {

    heaterEnabled = true;

    updateHeater();

    Serial.println("OK,HEATER,1");

    return;
  }


  // ==========================================================
  // HEATER OFF
  // ==========================================================

  if (cmd == "HEATER,OFF") {

    heaterEnabled = false;

    digitalWrite(
      PIN_RELAY_HEATER,
      RELAY_HEATER_OFF
    );

    Serial.println("OK,HEATER,0");

    return;
  }


  // ==========================================================
  // TESTE AUTOMATICO
  // TEST,duracao,pwmInicio,pwmFim
  // ==========================================================

  if (cmd.startsWith("TEST,")) {

    String args =
      cmd.substring(5);

    int p1 =
      args.indexOf(',');

    int p2 =
      args.indexOf(',', p1 + 1);


    if (
      p1 > 0 &&
      p2 > p1
    ) {

      unsigned long duration =
        args.substring(
          0,
          p1
        ).toInt();

      int pwmStart =
        args.substring(
          p1 + 1,
          p2
        ).toInt();

      int pwmEnd =
        args.substring(
          p2 + 1
        ).toInt();


      startAutomaticTest(
        duration,
        pwmStart,
        pwmEnd
      );

      return;
    }


    Serial.println(
      "ERROR,INVALID_TEST"
    );

    return;
  }


  // ==========================================================
  // UNKNOWN
  // ==========================================================

  Serial.print(
    "ERROR,UNKNOWN_COMMAND,"
  );

  Serial.println(cmd);
}


// ============================================================
// LE SERIAL
// ============================================================

void readSerial() {

  while (Serial.available()) {

    char c =
      Serial.read();


    if (c == '\n' || c == '\r') {

      if (serialBuffer.length() > 0) {

        processCommand(
          serialBuffer
        );

        serialBuffer = "";
      }

    } else {

      serialBuffer += c;


      if (serialBuffer.length() > 100) {
        serialBuffer = "";
      }
    }
  }
}


// ============================================================
// SETUP
// ============================================================

void setup() {

  Serial.begin(115200);


  // ==========================================================
  // PINOS
  // ==========================================================

  pinMode(
    PIN_HALL,
    INPUT_PULLUP
  );

  pinMode(
    PIN_BUTTON_LED,
    INPUT_PULLUP
  );

  pinMode(
    PIN_BUTTON_SMOKE,
    INPUT_PULLUP
  );

  pinMode(
    PIN_BUZZER,
    OUTPUT
  );

  pinMode(
    PIN_FAN_PWM,
    OUTPUT
  );

  pinMode(
    PIN_RELAY_LED,
    OUTPUT
  );

  pinMode(
    PIN_RELAY_SMOKE,
    OUTPUT
  );

  pinMode(
    PIN_RELAY_HEATER,
    OUTPUT
  );

  pinMode(
    PIN_LED_HEATER,
    OUTPUT
  );


  // ==========================================================
  // ESTADOS INICIAIS DOS RELES
  // ==========================================================

  // Luz: ativo HIGH -> OFF = LOW
  digitalWrite(
    PIN_RELAY_LED,
    RELAY_LED_OFF
  );


  // Fumaca: ativo LOW -> OFF = HIGH
  digitalWrite(
    PIN_RELAY_SMOKE,
    RELAY_SMOKE_OFF
  );


  // Aquecimento: ativo LOW -> ON
  digitalWrite(
    PIN_RELAY_HEATER,
    RELAY_HEATER_ON
  );


  // LED do aquecimento desligado
  digitalWrite(
    PIN_LED_HEATER,
    LOW
  );


  // ==========================================================
  // OLED
  // ==========================================================

  display.begin();

  display.clearBuffer();

  display.sendBuffer();


  // ==========================================================
  // HX711
  // ==========================================================

  scale.begin(
    PIN_HX_DT,
    PIN_HX_SCK
  );

  scale.set_scale(
    HX711_CALIBRATION
  );

  delay(300);

  if (scale.is_ready()) {
    scale.tare(10);
  }


  // ==========================================================
  // HALL
  // ==========================================================

  attachInterrupt(
    digitalPinToInterrupt(PIN_HALL),
    pulsoHall,
    FALLING
  );


  // ==========================================================
  // POT
  // ==========================================================

  lastPot1000 =
    readPot1000();


  // ==========================================================
  // STARTUP
  // ==========================================================

  startupSequence();


  // ==========================================================
  // INFORMACOES SERIAL
  // ==========================================================

  Serial.println(
    "READY,WIND_TUNNEL_2.0"
  );

  Serial.println(
    "BAUD,115200"
  );

  Serial.println(
    "POT,0..1000"
  );

  Serial.println(
    "RELAY_LED,ACTIVE_HIGH"
  );

  Serial.println(
    "RELAY_SMOKE,ACTIVE_LOW"
  );

  Serial.println(
    "RELAY_HEATER,ACTIVE_LOW"
  );

  Serial.println(
    "NTC,THERMISTOR_100K"
  );

  Serial.println(
    "NTC_BETA,3950"
  );

  Serial.println(
    "NTC_RESISTOR,100K"
  );

  Serial.println(
    "FORMAT,DATA,time_ms,pwm,rpm,speed_kmh,force_n,temp_c,fan,led,smoke"
  );


  lastRpmMs = millis();
}


// ============================================================
// LOOP
// ============================================================

void loop() {

  readSerial();

  updateLocalPot();

  updateButtons();

  updateTemperature();

  updateForce();

  updateRPM();

  updateAutomaticTest();

  updateSmokePulse();

  updateHeater();

  updateHeaterLED();


  // ==========================================================
  // OLED
  // ==========================================================

  if (
    millis() - lastDisplayMs >=
    DISPLAY_INTERVAL
  ) {

    lastDisplayMs = millis();

    drawMainScreen();
  }


  // ==========================================================
  // SERIAL DATA
  // ==========================================================

  if (
    millis() - lastDataMs >=
    DATA_INTERVAL
  ) {

    lastDataMs = millis();

    sendData();
  }
}