#include <stdlib.h> // strtol; being explicit rather than assuming Arduino.h pulls it in

const int PROTOCOL_VERSION = 0;

// Natural order: row position j maps straight to the board's INj+1
// (D2->IN1 etc.), proven by ink/bringup on this exact hardware. The
// "IN1-IN3-IN2-IN4" swap seen in Arduino tutorials only compensates for
// Stepper.h/AccelStepper's internal pattern, which we don't use.
const int PINS[3][4] = {
    { 2, 3, 4, 5 },
    { 6, 7, 8, 9 },
    { 10, 11, 12, 13 }
};

const byte HALFSTEP[8] = {
    0b1000,
    0b1100,
    0b0100,
    0b0110,
    0b0010,
    0b0011,
    0b0001,
    0b1001
};

const unsigned long WATCHDOG_MS = 500UL;      // no V command in this long -> all rates 0
const unsigned long COIL_RELEASE_MS = 2000UL; // idle this long -> de-energize that motor
const int MAX_STEPS_PER_LOOP = 8;             // catch-up bound per motor per pass

// int is 32-bit here (Renesas RA4M1), not 16-bit like classic AVR Uno.
int rate[3] = { 0, 0, 0 };
int phase[3] = { 0, 0, 0 };
unsigned long nextStepTime[3] = { 0, 0, 0 }; // micros() scale, per motor
unsigned long idleSince[3] = { 0, 0, 0 };    // millis() scale, per motor
bool coilsEnergized[3] = { false, false, false };
unsigned long lastCmdTime = 0;               // millis() scale, for the watchdog

// 1 Hz heartbeat while stepping - proves the V path is really counting.
unsigned long stepCount[3] = { 0, 0, 0 };
unsigned long lastHbMs = 0;

char lineBuf[48];
byte lineLen = 0;
bool lineOverflowed = false;

void printHello() {
    Serial.print("ink p");
    Serial.println(PROTOCOL_VERSION);
}

void setup() {
    Serial.begin(115200);
    printHello();
    for (int m = 0; m < 3; m++)
        for (int j = 0; j < 4; j++)
            pinMode(PINS[m][j], OUTPUT);
    unsigned long t = micros();
    for (int m = 0; m < 3; m++)
        nextStepTime[m] = t;
}

// Writes a 4-bit coil pattern to a motor's pins (IN1 = MSB). pattern=0
// de-energizes; pattern=HALFSTEP[phase[m]] steps.
void writeCoils(int motor, byte pattern) {
    for (int j = 0; j < 4; j++)
        digitalWrite(PINS[motor][j], (pattern >> (3 - j)) & 1);
    coilsEnergized[motor] = (pattern != 0);
}

void setRates(int r0, int r1, int r2) {
    int next[3] = { r0, r1, r2 };
    unsigned long nowUs = micros();
    for (int m = 0; m < 3; m++) {
        // Arm a fresh schedule on rising edge; park it while idle so a later
        // command can't inherit a multi-second stepping debt.
        if ((next[m] != 0 && rate[m] == 0) || next[m] == 0)
            nextStepTime[m] = nowUs;
        rate[m] = next[m];
        stepCount[m] = 0;
    }
    lastCmdTime = millis();
    lastHbMs = millis(); // align the heartbeat window to the new command
}

// Parses "V s1 s2 s3" and "P". Any malformed line is rejected whole - rate[]
// and lastCmdTime stay untouched, so corruption can't masquerade as a valid
// zero command and quietly defeat the watchdog.
void parseAndApply(char *buf) {
    // Answered on demand, not just at boot - native-USB boards drop the
    // whole connection on reset, so a fresh connection can't reliably catch
    // the one-shot setup() hello.
    if (buf[0] == 'P' && buf[1] == '\0') {
        printHello();
        return;
    }

    if (buf[0] != 'V' || buf[1] != ' ') return;

    long values[3];
    char *p = buf + 2;
    for (int i = 0; i < 3; i++) {
        char *endptr;
        values[i] = strtol(p, &endptr, 10);
        if (endptr == p) return; // no digits at all
        if (i < 2) {
            if (*endptr != ' ') return;
            p = endptr + 1;
        } else {
            if (*endptr != '\0') return;
        }
    }

    setRates((int)values[0], (int)values[1], (int)values[2]);
}

// Non-blocking, byte at a time - Serial.readStringUntil() blocks up to 1s
// with no newline and drops partial lines on timeout, which would freeze
// step timing and can corrupt commands under real jitter.
void handleSerial() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n') {
            if (!lineOverflowed) {
                lineBuf[lineLen] = '\0'; // strtol needs a real C string
                parseAndApply(lineBuf);
            }
            lineLen = 0;
            lineOverflowed = false;
        } else if (c == '\r') {
            // ignore CR so "V 1 2 3\r\n" still parses
        } else if (lineLen < sizeof(lineBuf) - 1) {
            lineBuf[lineLen++] = c;
        } else {
            lineOverflowed = true; // drop the rest, resync at the next '\n'
        }
    }
}

void applyWatchdog() {
    if (millis() - lastCmdTime > WATCHDOG_MS) {
        rate[0] = 0;
        rate[1] = 0;
        rate[2] = 0;
        // Never refresh lastCmdTime here - that would re-arm the dog.
    }
}

void stepMotors() {
    unsigned long nowMs = millis();
    unsigned long nowUs = micros();

    for (int m = 0; m < 3; m++) {
        if (rate[m] == 0) {
            nextStepTime[m] = nowUs; // park the schedule while idle
            if (coilsEnergized[m] && (nowMs - idleSince[m] > COIL_RELEASE_MS))
                writeCoils(m, 0);
            continue;
        }

        idleSince[m] = nowMs;
        unsigned long interval = 1000000UL / (unsigned long)abs(rate[m]);
        if (interval == 0) interval = 1;

        // When due, take up to MAX_STEPS_PER_LOOP owed steps; never snap
        // nextStepTime to "now" (that discards owed steps and skews the
        // long-run average rate that dead reckoning depends on).
        int guard = 0;
        while (nowUs >= nextStepTime[m] && guard++ < MAX_STEPS_PER_LOOP) {
            phase[m] = (phase[m] + (rate[m] > 0 ? 1 : -1)) & 7;
            writeCoils(m, HALFSTEP[phase[m]]);
            nextStepTime[m] += interval;
            stepCount[m]++;
        }
    }
}

void heartbeat() {
    unsigned long now = millis();
    if (now - lastHbMs < 1000UL) return;
    lastHbMs = now;
    // Only speak while commanded - proves the V path without flooding USB
    // when idle (an unread flood can block Serial.print and deadlock the
    // whole loop against a host that is itself blocked writing to us).
    if (rate[0] == 0 && rate[1] == 0 && rate[2] == 0)
        return;
    Serial.print("ink hb rate=");
    Serial.print(rate[0]);
    Serial.print(' ');
    Serial.print(rate[1]);
    Serial.print(' ');
    Serial.print(rate[2]);
    Serial.print(" steps=");
    Serial.print(stepCount[0]);
    Serial.print(' ');
    Serial.print(stepCount[1]);
    Serial.print(' ');
    Serial.println(stepCount[2]);
    stepCount[0] = stepCount[1] = stepCount[2] = 0;
}

void loop() {
    handleSerial();
    applyWatchdog();
    stepMotors();
    heartbeat();
}
