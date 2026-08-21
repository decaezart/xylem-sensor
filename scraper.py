import re
import time
from datetime import datetime, timedelta
from playwright.sync_api import sync_playwright


def scrape_clean_final():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    url = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"
    page.goto(url)

    print("[INFO] Menunggu halaman memuat seluruh data WebSocket...")
    time.sleep(15)

    page_text = page.inner_text("body")
    lines = [line.strip() for line in page_text.split("\n") if line.strip()]

    clean_results = set()

    # Kata kunci valid (Thermistor sengaja DIHAPUS)
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
    ]

    for i, line in enumerate(lines):
      if any(kw.lower() in line.lower() for kw in valid_keywords):
        sensor_info = line

        # Gabungkan baris ke bawah jika parameter terpecah (khusus Atmospheric atau lainnya)
        if (
            "TempAmbient" in line
            or "HumidityAmbient" in line
            or not any(
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
                    "DegreesC",
                ]
            )
        ):
          combined = line
          for j in range(1, 4):
            if i + j < len(lines):
              combined += " " + lines[i + j]
          sensor_info = combined

        # Validasi akhir satuan ukur
        if any(
            unit in sensor_info
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
                "DegreesC",
            ]
        ):

          def adjust_time(match):
            time_str = match.group(0)
            try:
              dt = datetime.strptime(time_str, "%H:%M:%S")
              adjusted_dt = dt - timedelta(hours=2)
              return adjusted_dt.strftime("%H:%M:%S")
            except:
              return time_str

          corrected_line = re.sub(r"\d{2}:\d{2}:\d{2}", adjust_time, sensor_info)
          clean_results.add(corrected_line)

    # Simpan ke nilaisensor.txt
    with open("nilaisensor.txt", "w", encoding="utf-8") as f:
      f.write(
          f"LAPORAN NILAI SENSOR - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
      )
      f.write("=" * 65 + "\n")
      for item in sorted(clean_results):
        f.write(item + "\n")

    print(
        "[INFO] Berhasil! Termistor dihapus, Atmospheric & sensor lain"
        " diperbarui."
    )
    browser.close()


if __name__ == "__main__":
  scrape_clean_final()
