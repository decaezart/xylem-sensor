import time
from playwright.sync_api import sync_playwright


def scrape_visual_only():
  with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    url = "https://public.eagle.io/public/dash/etpvkt0ofbbt6mt"
    page.goto(url)

    # Beri waktu lebih lama agar WebSocket selesai melakukan render visual di layar
    print("[INFO] Menunggu render visual data di layar...")
    time.sleep(15)

    # Mengambil teks khusus dari elemen kartu/widget yang terlihat di layar saja
    # Ini memastikan kita mengambil data yang sama persis seperti yang Anda lihat di screenshot
    cards = page.locator(".card, [class*='card']").all()

    clean_results = set()

    for card in cards:
      text = card.inner_text()
      if text:
        # Pecah per baris dalam satu kotak widget
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        # Cari baris yang memiliki format nilai sensor dan timestamp
        for line in lines:
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

    # Simpan ke nilaisensor.txt
    with open("nilaisensor.txt", "w", encoding="utf-8") as f:
      f.write(
          f"LAPORAN VISUAL SENSOR - {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
      )
      f.write("=" * 65 + "\n")
      for item in sorted(clean_results):
        f.write(item + "\n")

    print("[INFO] Selesai! Data visual layar berhasil diperbarui.")

    browser.close()


if __name__ == "__main__":
  scrape_visual_only()
