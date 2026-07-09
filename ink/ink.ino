const int PROTOCOL_VERSION = 0;

// The Nano R4's built-in LED is single-color, and its pin isn't a PWM
// channel on this core (same limitation as pin 13 on a classic Uno), so
// this is a plain on/off flash rather than a fade. True color-changing
// would need an RGB LED or NeoPixel, which isn't part of the current BOM.
const unsigned long FLASH_PERIOD_MS = 4000;

void setup() {
    Serial.begin(115200);
    Serial.print("ink p");
    Serial.println(PROTOCOL_VERSION);
    pinMode(LED_BUILTIN, OUTPUT);
}

void loop() {
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        Serial.print("echo: ");
        Serial.println(line);
    }

    // Non-blocking slow on/off flash, driven off millis() rather than
    // delay() so the serial echo above stays responsive.
    bool on = (millis() % FLASH_PERIOD_MS) < (FLASH_PERIOD_MS / 2);
    digitalWrite(LED_BUILTIN, on ? HIGH : LOW);
}
