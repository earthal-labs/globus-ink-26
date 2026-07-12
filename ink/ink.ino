const int PROTOCOL_VERSION = 0;

const int PINS[12] = { 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13 };

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

const unsigned long STEP_US = 1200;
const long STEPS_PER_REV = 4076;
const unsigned long PAUSE_MS = 1000;

int phase = 0;
unsigned long nextStep = 0;

enum TestState { FORWARD, PAUSE_AFTER_FORWARD, BACKWARD, PAUSE_AFTER_BACKWARD };
TestState state = FORWARD;
long stepsRemaining = STEPS_PER_REV;
unsigned long pauseUntil = 0;

void setup() {
    Serial.begin(115200);
    Serial.print("ink p");
    Serial.println(PROTOCOL_VERSION);
    for (int i = 0; i < 12; i++) {
        pinMode(PINS[i], OUTPUT);
    }
    nextStep = micros();
}

void step(int direction) {
    phase = (phase + direction) & 7;
    for (int i = 0; i < 12; i++)
        digitalWrite(PINS[i], (HALFSTEP[phase] >> (3 - (i % 4))) & 1);
}

void loop() {
    unsigned long now = micros();
    switch (state) {
        case FORWARD:
            if (now >= nextStep) {
                step(1);
                nextStep += STEP_US;
                if (--stepsRemaining <= 0) {
                    state = PAUSE_AFTER_FORWARD;
                    pauseUntil = millis() + PAUSE_MS;
                }
            }
            break;
        case PAUSE_AFTER_FORWARD:
            if (millis() >= pauseUntil) {
                stepsRemaining = STEPS_PER_REV;
                state = BACKWARD;
            }
            break;
        case BACKWARD:
            if (now >= nextStep) {
                step(-1);
                nextStep += STEP_US;
                if (--stepsRemaining <= 0) {
                    state = PAUSE_AFTER_BACKWARD;
                    pauseUntil = millis() + PAUSE_MS;
                }
            }
            break;
        case PAUSE_AFTER_BACKWARD:
            if (millis() >= pauseUntil) {
                stepsRemaining = STEPS_PER_REV;
                state = FORWARD;
            }
            break;
    }
}
