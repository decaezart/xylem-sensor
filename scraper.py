import time
from playwright.sync_api import sync_playwright


def scrape_fixed():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    url = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"
    page.goto(url)

    print("[INFO] Menunggu halaman memuat seluruh data WebSocket...")
    time.sleep(15)  # Waktu tunggu agar data stabil

    # Ambil seluruh teks dari halaman web
    page_text = page.inner_text("body")
    lines = [line.strip() for line in page_text.split("\n") if line.strip()]

    clean_results = set()

    # Kata kunci parameter utama sensor
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

    for line in lines:
      # Cek apakah baris mengandung parameter sensor DAN memiliki satuan pengukuran
      if any(kw.lower() in line.lower() for kw in valid_keywords):
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
          # Pastikan baris tersebut memuat informasi waktu (timestamp) agar jamnya akurat
          if ":" in line:
            clean_results.add(line)

    # Simpan ke nilaisensor.txt
    with open("nilaisensor.txt", "w", encoding="utf-8") as f:
      f.write(
          f"LAPORAN NILAI SENSOR - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
      )
      f.write("=" * 65 + "\n")
      for item in sorted(clean_results):
        f.write(item + "\n")

    print("[INFO] Berhasil! Data dan timestamp tersimpan dengan benar.")
    browser.close()


if __name__ == "__main__":
  scrape_fixed()
