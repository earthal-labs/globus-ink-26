#include <stdlib.h> // strtol; being explicit rather than assuming Arduino.h pulls it in
#include <string.h> // strcmp

const int PROTOCOL_VERSION = 0;

// Natural order: row position j maps straight to the board's INj+1 (D2->IN1
// etc.). FULLSTEP below is the 28BYJ-48's native sequential two-coil
// pattern, so no reordering is needed for production. The IN1-IN3-IN2-IN4
// swap (PINS_SWAP) only compensates for Arduino Stepper.h's internal
// pattern — but is offered as a bench mode in case a harness is wired that
// way. (A briefly-committed production swap made motors hum in place.)
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

// Full-step: two coils always on (max torque). Half-step: 8-phase table with
// single-coil phases (~30% less torque). STEPS_PER_RAD in tsup/config.py must
// match whichever mode is locked as production (default: full-step).
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

const unsigned long WATCHDOG_MS = 500UL;      // no V/T command in this long -> all rates 0
const unsigned long COIL_RELEASE_MS = 2000UL; // idle this long -> de-energize that motor
const unsigned long BENCH_HOLD_MS = 4000UL;   // T command self-hold (no host keepalive)
const unsigned long CRAWL_DWELL_MS = 500UL;   // per-phase dwell so LED walk is eye-visible
const unsigned long PROBE_HOLD_MS = 2000UL;   // single-IN LED probe duration
const int BENCH_RATE = 40;                   // steps/s for T
const int CRAWL_CYCLES = 2;                  // full revolutions of the phase table
const int RAMP_STEP = 5;                     // steps/s added toward |target| each ramp tick
const unsigned long RAMP_INTERVAL_MS = 20UL;  // => ~250 steps/s^2 cold-start accel

enum DriveMode : byte {
    MODE_NAT_FULL = 0,
    MODE_NAT_HALF = 1,
    MODE_SWAP_FULL = 2,
    MODE_SWAP_HALF = 3
};

DriveMode driveMode = MODE_NAT_FULL;

// int is 32-bit here (Renesas RA4M1), not 16-bit like classic AVR Uno.
int targetRate[3] = { 0, 0, 0 };             // commanded steps/s (from V / T)
int rate[3] = { 0, 0, 0 };                   // ramped steps/s actually used to step
int phase[3] = { 0, 0, 0 };
unsigned long nextStepTime[3] = { 0, 0, 0 }; // micros() scale, per motor
unsigned long idleSince[3] = { 0, 0, 0 };    // millis() scale, per motor
unsigned long lastRampMs[3] = { 0, 0, 0 };
unsigned long lastCmdTime = 0;               // millis() scale, for the watchdog
unsigned long benchUntil = 0;                // millis(); T/C/I hold watchdog until then

// Slow visual crawl: advances FULLSTEP one phase every CRAWL_DWELL_MS.
int crawlMotor = -1;
int crawlPhase = 0;
int crawlStepsLeft = 0;
unsigned long crawlNextMs = 0;

// Single-IN probe: lights exactly one driver input so wiring can be verified.
int probeMotor = -1;

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
            pinMode(PINS_NAT[m][j], OUTPUT); // NAT and SWAP use the same 12 pins
}

// Writes a 4-bit coil pattern to a motor's pins (IN1 = MSB under natural
// mapping). pattern=0 de-energizes.
void writeCoils(int motor, byte pattern) {
    const int (*pins)[4] = isSwapMode() ? PINS_SWAP : PINS_NAT;
    for (int j = 0; j < 4; j++)
        digitalWrite(pins[motor][j], (pattern >> (3 - j)) & 1);
}

void setTargets(int r0, int r1, int r2) {
    targetRate[0] = r0;
    targetRate[1] = r1;
    targetRate[2] = r2;
    lastCmdTime = millis();
}

void zeroTargets() {
    setTargets(0, 0, 0);
    rate[0] = rate[1] = rate[2] = 0;
}

void stopBenchEffects() {
    if (crawlMotor >= 0) writeCoils(crawlMotor, 0);
    if (probeMotor >= 0) writeCoils(probeMotor, 0);
    crawlMotor = -1;
    crawlStepsLeft = 0;
    probeMotor = -1;
}

void printPatternBits(byte pattern) {
    // IN1..IN4 as 1/0 so the host can confirm the eye-visible LED pair.
    for (int j = 0; j < 4; j++)
        Serial.print((pattern >> (3 - j)) & 1);
}

// Parses "V s1 s2 s3", "P", "D mode", "T m mode", "C m", "I m j".
// Malformed lines are rejected whole so corruption can't quietly defeat
// the watchdog.
void parseAndApply(char *buf) {
    if (buf[0] == 'P' && buf[1] == '\0') {
        printHello();
        return;
    }

    // "D mode" — select coil map for all subsequent stepping (bench + prod).
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

    // "C m" — slow full-step crawl so the LED pair walk is visible by eye.
    // Uses NATURAL pins + FULLSTEP only (production map). Self-held.
    if (buf[0] == 'C' && buf[1] == ' ') {
        char *endptr;
        long motor = strtol(buf + 2, &endptr, 10);
        if (endptr == buf + 2 || *endptr != '\0') return;
        if (motor < 0 || motor > 2) return;
        stopBenchEffects();
        zeroTargets();
        driveMode = MODE_NAT_FULL;
        for (int m = 0; m < 3; m++)
            writeCoils(m, 0);
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

    // "I m j" — light only motor m's INj (j=1..4) for PROBE_HOLD_MS.
    // Exactly one ULN LED must turn on; any other result = wiring fault.
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
        zeroTargets();
        driveMode = MODE_NAT_FULL;
        for (int m = 0; m < 3; m++)
            writeCoils(m, 0);
        probeMotor = (int)motor;
        byte pattern = (byte)(1 << (4 - inj)); // IN1=0b1000 .. IN4=0b0001
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

    // "T m mode" — self-held single-motor bench spin (no host keepalive).
    if (buf[0] == 'T' && buf[1] == ' ') {
        char *endptr;
        long motor = strtol(buf + 2, &endptr, 10);
        if (endptr == buf + 2 || *endptr != ' ') return;
        if (motor < 0 || motor > 2) return;
        DriveMode mode;
        if (!parseDriveMode(endptr + 1, &mode)) return;
        stopBenchEffects();
        driveMode = mode;
        phase[motor] &= phaseMask();
        int rates[3] = { 0, 0, 0 };
        rates[motor] = BENCH_RATE;
        setTargets(rates[0], rates[1], rates[2]);
        // Start the ramp near the bench rate so the LED walk is immediate,
        // but still allow ramp logic to kick in if target is later raised.
        rate[0] = rate[1] = rate[2] = 0;
        rate[motor] = BENCH_RATE;
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
        if (endptr == p) return; // no digits at all
        if (i < 2) {
            if (*endptr != ' ') return;
            p = endptr + 1;
        } else {
            if (*endptr != '\0') return;
        }
    }

    stopBenchEffects();
    setTargets((int)values[0], (int)values[1], (int)values[2]);
    benchUntil = 0; // host-driven V takes over from any self-held bench
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
    if (millis() < benchUntil) return; // T/C/I self-hold still active
    if (millis() - lastCmdTime > WATCHDOG_MS) {
        if (probeMotor >= 0) {
            writeCoils(probeMotor, 0);
            probeMotor = -1;
        }
        stopBenchEffects();
        zeroTargets();
    }
}

void runCrawl() {
    if (crawlMotor < 0) return;
    unsigned long now = millis();
    if (now < crawlNextMs) return;

    if (crawlStepsLeft <= 0) {
        writeCoils(crawlMotor, 0);
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
    Serial.println(" (expect exactly two adjacent LEDs)");

    crawlPhase = (crawlPhase + 1) & 3;
    crawlStepsLeft--;
    crawlNextMs = now + CRAWL_DWELL_MS;
    idleSince[crawlMotor] = now;
}

// Ramps |rate| toward |targetRate| so cold 28BYJ-48 starts don't stall when
// tsup jumps straight to a high command (no AccelStepper — same idea in-line).
void applyRamp() {
    unsigned long now = millis();
    for (int m = 0; m < 3; m++) {
        int target = targetRate[m];
        if (target == 0) {
            rate[m] = 0;
            lastRampMs[m] = now;
            continue;
        }
        // Sign flip: snap through zero and ramp up in the new direction.
        if (rate[m] != 0 && ((rate[m] > 0) != (target > 0))) {
            rate[m] = 0;
            lastRampMs[m] = now;
            continue;
        }
        if (now - lastRampMs[m] < RAMP_INTERVAL_MS) continue;
        lastRampMs[m] = now;

        int absTarget = abs(target);
        int absRate = abs(rate[m]);
        int sign = target > 0 ? 1 : -1;
        if (absRate < absTarget) {
            absRate += RAMP_STEP;
            if (absRate > absTarget) absRate = absTarget;
            // Don't crawl at 1 step/s under load — start usefully.
            if (absRate < RAMP_STEP) absRate = RAMP_STEP;
            if (absRate > absTarget) absRate = absTarget;
        } else if (absRate > absTarget) {
            absRate = absTarget;
        }
        rate[m] = sign * absRate;
    }
}

void stepMotors() {
    // Crawl/probe own the coil lines; don't let the rate scheduler fight them.
    if (crawlMotor >= 0 || probeMotor >= 0) return;

    unsigned long nowMs = millis();
    unsigned long nowUs = micros();
    const byte *table = stepTable();
    int mask = phaseMask();

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
            phase[m] = (phase[m] + (rate[m] > 0 ? 1 : -1)) & mask;
            writeCoils(m, table[phase[m]]);
            nextStepTime[m] += interval; // += , not = : no drift
        }
    }
}

void loop() {
    handleSerial();
    applyWatchdog();
    runCrawl();
    applyRamp();
    stepMotors();
}
