#include <stdlib.h> // strtol
#include <string.h> // strcmp

const int PROTOCOL_VERSION = 0;

// Production = NAT pins + HALFSTEP, matching ink/bringup (the path that
// demonstrably rotates these motors). FULLSTEP / pin-swap remain bench-only.
const int PINS_NAT[3][4] = {
    { 2, 3, 4, 5 },
    { 6, 7, 8, 9 },
    { 10, 11, 12, 13 }
};
const int PINS_SWAP[3][4] = {
    { 2, 4, 3, 5 },
    { 6, 8, 7, 9 },
    { 10, 12, 11, 13 }
};

const byte FULLSTEP[4] = {
    0b1100,
    0b0110,
    0b0011,
    0b1001
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

const unsigned long WATCHDOG_MS = 500UL;
const unsigned long COIL_RELEASE_MS = 2000UL;
const unsigned long BENCH_HOLD_MS = 4000UL;
const unsigned long CRAWL_DWELL_MS = 500UL;
const unsigned long PROBE_HOLD_MS = 2000UL;
const unsigned long BRINGUP_STEP_US = 1200UL; // ink/bringup STEP_US
const int BENCH_RATE = 833;                  // ~1e6 / BRINGUP_STEP_US
const int CRAWL_CYCLES = 2;
const int MAX_STEPS_PER_LOOP = 8;            // catch-up bound (bringup bursts)

enum DriveMode : byte {
    MODE_NAT_FULL = 0,
    MODE_NAT_HALF = 1,
    MODE_SWAP_FULL = 2,
    MODE_SWAP_HALF = 3
};

DriveMode driveMode = MODE_NAT_HALF;

int rate[3] = { 0, 0, 0 };                   // commanded steps/s (no ramp)
int phase[3] = { 0, 0, 0 };
unsigned long nextStepTime[3] = { 0, 0, 0 };
unsigned long idleSince[3] = { 0, 0, 0 };
bool coilsEnergized[3] = { false, false, false };
unsigned long lastCmdTime = 0;
unsigned long benchUntil = 0;

// Slow visual crawl (FULLSTEP, natural pins).
int crawlMotor = -1;
int crawlPhase = 0;
int crawlStepsLeft = 0;
unsigned long crawlNextMs = 0;

int probeMotor = -1;

// In-firmware copy of ink/bringup — bypasses the V rate scheduler entirely.
int freerunMotor = -1;
int freerunDir = 1;
int freerunPhase = 0;
unsigned long freerunNextUs = 0;

// Heartbeat so the host can prove the V path is actually stepping.
unsigned long stepCount[3] = { 0, 0, 0 };
unsigned long lastHbMs = 0;

char lineBuf[48];
byte lineLen = 0;
bool lineOverflowed = false;

bool isHalfMode() {
    return driveMode == MODE_NAT_HALF || driveMode == MODE_SWAP_HALF;
}

bool isSwapMode() {
    return driveMode == MODE_SWAP_FULL || driveMode == MODE_SWAP_HALF;
}

int phaseMask() {
    return isHalfMode() ? 7 : 3;
}

const byte *stepTable() {
    return isHalfMode() ? HALFSTEP : FULLSTEP;
}

void printHello() {
    Serial.print("ink p");
    Serial.println(PROTOCOL_VERSION);
}

void printDriveMode() {
    Serial.print("ink d ");
    switch (driveMode) {
        case MODE_NAT_FULL:  Serial.println("nat_full");  break;
        case MODE_NAT_HALF:  Serial.println("nat_half");  break;
        case MODE_SWAP_FULL: Serial.println("swap_full"); break;
        case MODE_SWAP_HALF: Serial.println("swap_half"); break;
    }
}

bool parseDriveMode(const char *name, DriveMode *out) {
    if (strcmp(name, "nat_full") == 0)  { *out = MODE_NAT_FULL;  return true; }
    if (strcmp(name, "nat_half") == 0)  { *out = MODE_NAT_HALF;  return true; }
    if (strcmp(name, "swap_full") == 0) { *out = MODE_SWAP_FULL; return true; }
    if (strcmp(name, "swap_half") == 0) { *out = MODE_SWAP_HALF; return true; }
    return false;
}

void setup() {
    Serial.begin(115200);
    printHello();
    for (int m = 0; m < 3; m++)
        for (int j = 0; j < 4; j++)
            pinMode(PINS_NAT[m][j], OUTPUT);
    unsigned long t = micros();
    for (int m = 0; m < 3; m++)
        nextStepTime[m] = t;
}

void writeCoils(int motor, byte pattern) {
    const int (*pins)[4] = isSwapMode() ? PINS_SWAP : PINS_NAT;
    for (int j = 0; j < 4; j++)
        digitalWrite(pins[motor][j], (pattern >> (3 - j)) & 1);
    coilsEnergized[motor] = (pattern != 0);
}

// Bringup-identical write: always NAT pins + HALFSTEP (ignores D mode).
void writeBringupStep(int motor, int ph) {
    for (int j = 0; j < 4; j++)
        digitalWrite(PINS_NAT[motor][j], (HALFSTEP[ph & 7] >> (3 - j)) & 1);
    coilsEnergized[motor] = true;
}

void releaseMotor(int motor) {
    for (int j = 0; j < 4; j++)
        digitalWrite(PINS_NAT[motor][j], LOW);
    coilsEnergized[motor] = false;
}

void setRates(int r0, int r1, int r2) {
    int next[3] = { r0, r1, r2 };
    unsigned long nowUs = micros();
    for (int m = 0; m < 3; m++) {
        // Rising edge from idle: arm the schedule like bringup's setup().
        if (rate[m] == 0 && next[m] != 0)
            nextStepTime[m] = nowUs;
        rate[m] = next[m];
        if (next[m] == 0)
            phase[m] &= phaseMask();
    }
    lastCmdTime = millis();
}

void zeroRates() {
    rate[0] = rate[1] = rate[2] = 0;
}

void stopBenchEffects() {
    if (crawlMotor >= 0) {
        releaseMotor(crawlMotor);
        crawlMotor = -1;
        crawlStepsLeft = 0;
    }
    if (probeMotor >= 0) {
        releaseMotor(probeMotor);
        probeMotor = -1;
    }
    if (freerunMotor >= 0) {
        releaseMotor(freerunMotor);
        freerunMotor = -1;
    }
}

void printPatternBits(byte pattern) {
    for (int j = 0; j < 4; j++)
        Serial.print((pattern >> (3 - j)) & 1);
}

void parseAndApply(char *buf) {
    if (buf[0] == 'P' && buf[1] == '\0') {
        printHello();
        return;
    }

    if (buf[0] == 'D' && buf[1] == ' ') {
        DriveMode mode;
        if (!parseDriveMode(buf + 2, &mode)) return;
        driveMode = mode;
        for (int m = 0; m < 3; m++)
            phase[m] &= phaseMask();
        printDriveMode();
        lastCmdTime = millis();
        return;
    }

    // "B m" — in-firmware ink/bringup clone (NAT+HALFSTEP @ 1200 µs, 4 s).
    if (buf[0] == 'B' && buf[1] == ' ') {
        char *endptr;
        long motor = strtol(buf + 2, &endptr, 10);
        if (endptr == buf + 2 || *endptr != '\0') return;
        if (motor < 0 || motor > 2) return;
        stopBenchEffects();
        zeroRates();
        for (int m = 0; m < 3; m++)
            releaseMotor(m);
        freerunMotor = (int)motor;
        freerunDir = 1;
        freerunPhase = 0;
        freerunNextUs = micros();
        benchUntil = millis() + BENCH_HOLD_MS;
        lastCmdTime = millis();
        Serial.print("ink b m=");
        Serial.print(freerunMotor);
        Serial.println(" bringup-clone 4s");
        return;
    }

    if (buf[0] == 'C' && buf[1] == ' ') {
        char *endptr;
        long motor = strtol(buf + 2, &endptr, 10);
        if (endptr == buf + 2 || *endptr != '\0') return;
        if (motor < 0 || motor > 2) return;
        stopBenchEffects();
        zeroRates();
        driveMode = MODE_NAT_FULL;
        for (int m = 0; m < 3; m++)
            releaseMotor(m);
        crawlMotor = (int)motor;
        crawlPhase = 0;
        crawlStepsLeft = 4 * CRAWL_CYCLES;
        crawlNextMs = millis();
        benchUntil = millis() + (unsigned long)crawlStepsLeft * CRAWL_DWELL_MS + 500UL;
        lastCmdTime = millis();
        Serial.print("ink c m=");
        Serial.println(crawlMotor);
        return;
    }

    if (buf[0] == 'I' && buf[1] == ' ') {
        char *endptr;
        long motor = strtol(buf + 2, &endptr, 10);
        if (endptr == buf + 2 || *endptr != ' ') return;
        if (motor < 0 || motor > 2) return;
        char *injStart = endptr + 1;
        long inj = strtol(injStart, &endptr, 10);
        if (endptr == injStart || *endptr != '\0') return;
        if (inj < 1 || inj > 4) return;
        stopBenchEffects();
        zeroRates();
        driveMode = MODE_NAT_FULL;
        for (int m = 0; m < 3; m++)
            releaseMotor(m);
        probeMotor = (int)motor;
        byte pattern = (byte)(1 << (4 - inj));
        writeCoils(probeMotor, pattern);
        benchUntil = millis() + PROBE_HOLD_MS;
        lastCmdTime = millis();
        Serial.print("ink i m=");
        Serial.print(probeMotor);
        Serial.print(" IN");
        Serial.print((int)inj);
        Serial.print(" bits=");
        printPatternBits(pattern);
        Serial.println();
        return;
    }

    if (buf[0] == 'T' && buf[1] == ' ') {
        char *endptr;
        long motor = strtol(buf + 2, &endptr, 10);
        if (endptr == buf + 2 || *endptr != ' ') return;
        if (motor < 0 || motor > 2) return;
        DriveMode mode;
        if (!parseDriveMode(endptr + 1, &mode)) return;
        stopBenchEffects();
        driveMode = mode;
        int rates[3] = { 0, 0, 0 };
        rates[motor] = BENCH_RATE;
        setRates(rates[0], rates[1], rates[2]);
        benchUntil = millis() + BENCH_HOLD_MS;
        printDriveMode();
        Serial.print("ink t m=");
        Serial.print((int)motor);
        Serial.print(" rate=");
        Serial.println(BENCH_RATE);
        return;
    }

    if (buf[0] != 'V' || buf[1] != ' ') return;

    long values[3];
    char *p = buf + 2;
    for (int i = 0; i < 3; i++) {
        char *endptr;
        values[i] = strtol(p, &endptr, 10);
        if (endptr == p) return;
        if (i < 2) {
            if (*endptr != ' ') return;
            p = endptr + 1;
        } else {
            if (*endptr != '\0') return;
        }
    }

    stopBenchEffects();
    setRates((int)values[0], (int)values[1], (int)values[2]);
    benchUntil = 0;
}

void handleSerial() {
    while (Serial.available()) {
        char c = Serial.read();
        if (c == '\n') {
            if (!lineOverflowed) {
                lineBuf[lineLen] = '\0';
                parseAndApply(lineBuf);
            }
            lineLen = 0;
            lineOverflowed = false;
        } else if (c == '\r') {
            // ignore CR so "V 833 0 0\r\n" still parses
        } else if (lineLen < sizeof(lineBuf) - 1) {
            lineBuf[lineLen++] = c;
        } else {
            lineOverflowed = true;
        }
    }
}

void applyWatchdog() {
    if (millis() < benchUntil) return;
    if (millis() - lastCmdTime > WATCHDOG_MS) {
        stopBenchEffects();
        zeroRates();
        // do NOT refresh lastCmdTime here — that used to re-arm the dog
    }
}

void runCrawl() {
    if (crawlMotor < 0) return;
    unsigned long now = millis();
    if (now < crawlNextMs) return;

    if (crawlStepsLeft <= 0) {
        releaseMotor(crawlMotor);
        Serial.println("ink c done");
        crawlMotor = -1;
        return;
    }

    byte pattern = FULLSTEP[crawlPhase & 3];
    writeCoils(crawlMotor, pattern);
    Serial.print("ink c phase=");
    Serial.print(crawlPhase & 3);
    Serial.print(" bits=");
    printPatternBits(pattern);
    Serial.println();

    crawlPhase = (crawlPhase + 1) & 3;
    crawlStepsLeft--;
    crawlNextMs = now + CRAWL_DWELL_MS;
    idleSince[crawlMotor] = now;
}

// Exact ink/bringup scheduler, hosted inside production firmware.
void runFreerun() {
    if (freerunMotor < 0) return;
    if (millis() >= benchUntil) {
        releaseMotor(freerunMotor);
        Serial.println("ink b done");
        freerunMotor = -1;
        return;
    }

    unsigned long now = micros();
    // Burst catch-up like bringup (soft-stall bound per loop).
    int guard = 0;
    while (now >= freerunNextUs && guard++ < MAX_STEPS_PER_LOOP) {
        freerunPhase = (freerunPhase + freerunDir) & 7;
        writeBringupStep(freerunMotor, freerunPhase);
        freerunNextUs += BRINGUP_STEP_US;
        stepCount[freerunMotor]++;
        idleSince[freerunMotor] = millis();
    }
}

void stepMotors() {
    if (crawlMotor >= 0 || probeMotor >= 0 || freerunMotor >= 0) return;

    unsigned long nowMs = millis();
    unsigned long nowUs = micros();
    const byte *table = stepTable();
    int mask = phaseMask();

    for (int m = 0; m < 3; m++) {
        if (rate[m] == 0) {
            if (coilsEnergized[m] && (nowMs - idleSince[m] > COIL_RELEASE_MS))
                releaseMotor(m);
            continue;
        }

        idleSince[m] = nowMs;
        unsigned long interval = 1000000UL / (unsigned long)abs(rate[m]);
        if (interval == 0) interval = 1;

        // Bringup-style: when due, take up to MAX_STEPS_PER_LOOP; NEVER snap
        // nextStepTime forward to "now" (that discarded owed steps and is the
        // largest behavioural gap vs the working bringup sketch).
        int guard = 0;
        while (nowUs >= nextStepTime[m] && guard++ < MAX_STEPS_PER_LOOP) {
            phase[m] = (phase[m] + (rate[m] > 0 ? 1 : -1)) & mask;
            writeCoils(m, table[phase[m]]);
            nextStepTime[m] += interval;
            stepCount[m]++;
        }
    }
}

void heartbeat() {
    unsigned long now = millis();
    if (now - lastHbMs < 1000UL) return;
    lastHbMs = now;
    // Only speak when something is actively commanded — proves V path
    // without flooding USB when idle.
    if (rate[0] == 0 && rate[1] == 0 && rate[2] == 0 && freerunMotor < 0)
        return;
    Serial.print("ink hb rate=");
    Serial.print(rate[0]);
    Serial.print(' ');
    Serial.print(rate[1]);
    Serial.print(' ');
    Serial.print(rate[2]);
    Serial.print(" steps/s≈");
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
    runCrawl();
    runFreerun();
    stepMotors();
    heartbeat();
}
