#include <Arduino.h>
#include <string.h>
#include <stdlib.h>

// 28BYJ-48 + ULN2003: IN1=D8, IN2=D9, IN3=D10, IN4=D11.
// Bench jogs only: no homing, limit switches, position feedback or load holding.
static char command[32];
static uint8_t length = 0;
static bool overflow = false;
static bool ledOn = false;
static const uint8_t motorPins[] = {8, 9, 10, 11};
static const uint8_t phases[] = {0x1, 0x3, 0x2, 0x6, 0x4, 0xC, 0x8, 0x9};
static const unsigned long stepIntervalUs = 4000;
static const unsigned long maxMoveMs = 3000;
static uint16_t remaining = 0;
static bool motorActive = false;
static int8_t direction = 1;
static uint8_t phase = 0;
static unsigned long lastStepUs = 0;
static unsigned long moveStartedMs = 0;

void stopMotor() {
  remaining = 0;
  motorActive = false;
  for (uint8_t pin : motorPins) digitalWrite(pin, LOW);
}

void tickMotor() {
  if (!motorActive) return;
  if (millis() - moveStartedMs >= maxMoveMs) {
    stopMotor();
    return;
  }
  const unsigned long now = micros();
  if (now - lastStepUs < stepIntervalUs) return;
  if (remaining == 0) {
    stopMotor();
    return;
  }
  lastStepUs = now;
  phase = (phase + direction + 8) % 8;
  for (uint8_t i = 0; i < 4; ++i) {
    digitalWrite(motorPins[i], (phases[phase] >> i) & 1);
  }
  --remaining;
  // Keep the final phase energized for its full dwell time before releasing.
}

void handleCommand() {
  command[length] = '\0';
  if (strcmp(command, "PING") == 0) {
    Serial.println(F("PONG AGENTCAM UNO 2"));
  } else if (strcmp(command, "STOP") == 0) {
    stopMotor();
    Serial.println(F("OK STOP"));
  } else if (strcmp(command, "MOTOR STATUS") == 0) {
    Serial.print(motorActive ? F("MOTOR MOVING ") : F("MOTOR IDLE "));
    Serial.println(remaining);
  } else if (strncmp(command, "MOVE ", 5) == 0) {
    char *end;
    const long steps = strtol(command + 5, &end, 10);
    if (end == command + 5 || *end != '\0' || steps == 0 || steps < -512 || steps > 512) {
      Serial.println(F("ERR STEPS_RANGE"));
    } else if (motorActive) {
      Serial.println(F("ERR BUSY"));
    } else {
      direction = steps > 0 ? 1 : -1;
      remaining = static_cast<uint16_t>(steps > 0 ? steps : -steps);
      motorActive = true;
      lastStepUs = micros();
      moveStartedMs = millis();
      Serial.print(F("OK MOVE "));
      Serial.println(steps);
    }
  } else if (strcmp(command, "LED ON") == 0) {
    ledOn = true;
    digitalWrite(LED_BUILTIN, HIGH);
    Serial.println(F("OK LED ON"));
  } else if (strcmp(command, "LED OFF") == 0) {
    ledOn = false;
    digitalWrite(LED_BUILTIN, LOW);
    Serial.println(F("OK LED OFF"));
  } else if (strcmp(command, "STATUS") == 0) {
    Serial.println(ledOn ? F("STATUS LED ON") : F("STATUS LED OFF"));
  } else if (length > 0) {
    Serial.println(F("ERR UNKNOWN_COMMAND"));
  }
}

void setup() {
  for (uint8_t pin : motorPins) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  pinMode(LED_BUILTIN, OUTPUT);
  digitalWrite(LED_BUILTIN, LOW);
  Serial.begin(115200);
  Serial.println(F("READY AGENTCAM UNO 2"));
}

void loop() {
  // Limit serial work so incoming data cannot starve stepping or its deadline.
  for (uint8_t count = 0; count < 32 && Serial.available() > 0; ++count) {
    const char input = static_cast<char>(Serial.read());
    if (input == '\r') continue;
    if (input == '\n') {
      if (overflow) Serial.println(F("ERR COMMAND_TOO_LONG"));
      else handleCommand();
      length = 0;
      overflow = false;
    } else if (!overflow) {
      if (length < sizeof(command) - 1) command[length++] = input;
      else overflow = true;
    }
  }
  tickMotor();
}
