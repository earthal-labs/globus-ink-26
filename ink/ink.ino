const int PROTOCOL_VERSION = 0;

void setup() {
    Serial.begin(115200);
    Serial.print("ink p");
    Serial.println(PROTOCOL_VERSION);
}

void loop() {
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        Serial.print("echo: ");
        Serial.println(line);
    }
}
