/*
 * Parda hisoblash algoritmi (yakuniy — karset + kalso bilan)
 * ------------------------------------------------------------------
 * Xona obyekti:
 *   {
 *     nom:        "Mehmonxona",
 *     eni:        3.0,           // metr
 *     boyi:       3.0,           // metr
 *     karnizQator:3,             // 1 | 2 | 3  (default parda tanlovi uchun)
 *     karnizTuri: "turba",       // "pataloshniy" | "turba" | "rils"
 *     tanlangan:  ["zashitniy","tul","parter"], // bo'sh bo'lsa qatordan olinadi
 *     tulCoef:    2,             // ixtiyoriy — kiritilmasa turdan avtomatik
 *     narx: {                    // qo'lda kiritiladi
 *       zashitniy:0, tul:0, parter:0, // mato: som/metr
 *       karsaj:0,                       // karset/lenta: som/metr (yagona)
 *       kalso:0                         // halqa: som/dona (yagona)
 *     }
 *   }
 *
 * QOIDALAR
 *   Mato uzunligi (barcha karniz turida bir xil):
 *     zashitniy = eni + 0.20
 *     tul       = eni × K + 0.20
 *     parter    = (boyi + 0.20) × 2
 *   Tul koeffitsienti K (turdan avtomatik, o'zgartirsa bo'ladi):
 *     pataloshniy → 3,  rils → 2.5,  turba → 2
 *
 *   Aksessuar (karset = lenta, m;  kalso = halqa, dona):
 *     PATALOSHNIY va RILS (bir xil):
 *       zashitniy: karset = eni,  kalso = 0
 *       tul:       karset = eni,  kalso = 0
 *       parter:    karset = 1.8,  kalso = 0
 *     TURBA:
 *       zashitniy: karset = 0,        kalso = eni ÷ 0.10   (har 10 sm 1 ta)
 *       tul:       karset = mato,     kalso = mato ÷ 0.15  (har 15 sm 1 ta)
 *       parter:    karset = 6.0,      kalso = 36           (doimiy)
 *
 *   Summa = mato×narx_mato + karset×narx_karsaj + kalso×narx_kalso
 */

const HEM = 0.20;
const COEF_DEFAULT = { pataloshniy: 3, turba: 2, rils: 2.5 };
const PARDA_TARTIBI = ["zashitniy", "tul", "parter"]; // qator 1,2,3

function round2(n) { return Math.round(n * 100) / 100; }

// har `oraliq` (m) da 1 dona, yuqoriga yaxlitlangan (float xatosiga chidamli)
function kalsoSoni(uzunlik, oraliq) {
  return Math.ceil(round2(uzunlik) / oraliq - 1e-9);
}

function defaultTanlov(karnizQator) {
  return PARDA_TARTIBI.slice(0, karnizQator);
}

// Mato uzunligi (m)
function matoUzunligi(tur, x, coef) {
  if (tur === "zashitniy") return x.eni + HEM;
  if (tur === "tul") return x.eni * coef + HEM;
  if (tur === "parter") return (x.boyi + HEM) * 2;
  return 0;
}

// Aksessuar: { karset (m), kalso (dona) }
function aksessuar(tur, x, mato) {
  const t = x.karnizTuri;
  if (t === "pataloshniy" || t === "rils") {
    if (tur === "parter") return { karset: 1.8, kalso: 0 };
    return { karset: x.eni, kalso: 0 };            // zashitniy / tul
  }
  // turba
  if (tur === "zashitniy") return { karset: 0, kalso: kalsoSoni(x.eni, 0.10) };
  if (tur === "tul") return { karset: mato, kalso: kalsoSoni(mato, 0.15) };
  if (tur === "parter") return { karset: 6.0, kalso: 36 };
  return { karset: 0, kalso: 0 };
}

// Bitta xonani hisoblaydi
function hisoblaXona(x) {
  const coef = x.tulCoef != null ? x.tulCoef : COEF_DEFAULT[x.karnizTuri];
  const tanlangan = x.tanlangan && x.tanlangan.length
    ? x.tanlangan
    : defaultTanlov(x.karnizQator);
  const narx = x.narx || {};

  const qatorlar = tanlangan.map((tur) => {
    const mato = matoUzunligi(tur, x, coef);
    const { karset, kalso } = aksessuar(tur, x, mato);
    const summa =
      mato * (narx[tur] || 0) +
      karset * (narx.karsaj || 0) +
      kalso * (narx.kalso || 0);
    return {
      tur,
      coef: tur === "tul" ? coef : undefined,
      mato: round2(mato),
      karset: round2(karset),
      kalso,
      summa: Math.round(summa),
    };
  });

  const xonaSummasi = qatorlar.reduce((s, q) => s + q.summa, 0);
  return { nom: x.nom, qatorlar, xonaSummasi };
}

// Bir nechta xona + umumiy jami
function hisoblaHammasi(xonalar) {
  const natijalar = xonalar.map(hisoblaXona);
  const umumiyJami = natijalar.reduce((s, r) => s + r.xonaSummasi, 0);
  return { natijalar, umumiyJami };
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    HEM, COEF_DEFAULT, defaultTanlov,
    matoUzunligi, aksessuar, kalsoSoni,
    hisoblaXona, hisoblaHammasi,
  };
}
