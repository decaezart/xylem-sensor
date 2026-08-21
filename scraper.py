import time
from playwright.sync_api import sync_playwright


def scrape_clean_final():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    url = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"
    page.goto(url)
    time.sleep(12)  # Tunggu WebSocket memuat data

    page_text = page.inner_text("body")
    lines = [line.strip() for line in page_text.split("\n") if line.strip()]

    clean_results = set()

    # Kata kunci parameter utama yang pasti memiliki nilai pengukuran
    valid_keywords = [
        "BatteryVoltage",
        "CurrentMaximum",
        "InternalTemperature",
        "InternalHumidity",
        "TempAmbient",
        "HumidityAmbient",
        "Salinity",
        "Turbidity",
        "ODO Sat",
        "External Temp",
        "Chlorophyll",
        "BGA PC",
        "fDOM",
        "Thermistor",
    ]

    for i, line in enumerate(lines):
      # Cek apakah baris mengandung parameter sensor dan memiliki indikator nilai (seperti Volts, %, °C, FNU, ug/L, Deg C, dll.)
      if any(kw.lower() in line.lower() for kw in valid_keywords):
        # Ambil baris jika mengandung angka atau status NORMAL
        if any(
            unit in line
            for unit in [
                "Volts",
                "mA",
                "°C",
                "%",
                "FNU",
                "ppm",
                "ug/L",
                "RFU",
                "Deg C",
            ]
        ):
          clean_results.add(line)

    # Simpan ke nilaisensor.txt dengan rapi
    with open("nilaisensor.txt", "w", encoding="utf-8") as f:
      f.write(
          f"LAPORAN BERSIH NILAI SENSOR - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
      )
      f.write("=" * 65 + "\n")
      for item in sorted(clean_results):
        f.write(item + "\n")

    print("[INFO] Berhasil! Data bersih telah diperbarui di 'nilaisensor.txt'")


if __name__ == "__main__":
  scrape_clean_final()