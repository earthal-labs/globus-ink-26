#include <stdlib.h> // strtol; being explicit rather than assuming Arduino.h pulls it in

const int PROTOCOL_VERSION = 0;

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

// int is 32-bit here (Renesas RA4M1), not 16-bit like classic AVR Uno.
int rate[3] = { 0, 0, 0 };
int phase[3] = { 0, 0, 0 };
unsigned long nextStepTime[3] = { 0, 0, 0 }; // micros() scale, per motor
unsigned long idleSince[3] = { 0, 0, 0 };    // millis() scale, per motor
unsigned long lastCmdTime = 0;               // millis() scale, for the watchdog

char lineBuf[40];
byte lineLen = 0;
bool lineOverflowed = false;

void setup() {
    Serial.begin(115200);
    Serial.print("ink p");
    Serial.println(PROTOCOL_VERSION);
    for (int m = 0; m < 3; m++)
        for (int j = 0; j < 4; j++)
            pinMode(PINS[m][j], OUTPUT);
}

// Writes a 4-bit coil pattern to a motor's pins (IN1 = MSB). pattern=0
// de-energizes; pattern=HALFSTEP[phase[m]] steps.
void writeCoils(int motor, byte pattern) {
    for (int j = 0; j < 4; j++)
        digitalWrite(PINS[motor][j], (pattern >> (3 - j)) & 1);
}

// Parses "V s1 s2 s3". Any malformed line is rejected whole - rate[] and
// lastCmdTime stay untouched, so corruption can't masquerade as a valid
// zero command and quietly defeat the watchdog.
void parseAndApply(char *buf) {
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

    rate[0] = (int)values[0];
    rate[1] = (int)values[1];
    rate[2] = (int)values[2];
    lastCmdTime = millis();
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
    }
}

void stepMotors() {
    unsigned long nowMs = millis();
    unsigned long nowUs = micros();

    for (int m = 0; m < 3; m++) {
        if (rate[m] == 0) {
            if (nowMs - idleSince[m] > COIL_RELEASE_MS) writeCoils(m, 0);
            continue;
        }

        idleSince[m] = nowMs; // refreshed each active tick; freezes at the
                               // last active moment once rate hits 0

        unsigned long interval = 1000000UL / (unsigned long)abs(rate[m]);

        // Resuming from idle leaves the schedule stale; without this the
        // step below would burst-fire to "catch up," which the motor
        // can't physically do and which tsup's dead reckoning never sees
        // (real uncommanded steps => drift, sec. 5.2's failure mode).
        if (nowUs - nextStepTime[m] > interval) {
            nextStepTime[m] = nowUs;
        }

        if (nowUs >= nextStepTime[m]) {
            phase[m] = (phase[m] + (rate[m] > 0 ? 1 : -1)) & 7;
            writeCoils(m, HALFSTEP[phase[m]]);
            nextStepTime[m] += interval; // += , not = : no drift
        }
    }
}

void loop() {
    handleSerial();
    applyWatchdog();
    stepMotors();
}
