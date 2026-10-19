#!/usr/bin/env python3
"""Actualiza datos.json sin intervención humana.

Fuentes:
- AEMET OpenData (necesita el secreto AEMET_API_KEY):
  observación horaria de las estaciones de Ibiza y Formentera,
  avisos (CAP) para la zona Ibiza y Formentera y
  probabilidad de lluvia por municipio.
- Titulares de la prensa local por RSS (sin clave).

Solo toca los campos automáticos; respeta los avisos e incidencias
escritos a mano. Si una fuente falla, deja el dato anterior.
"""
import datetime as dt
import email.utils
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARCHIVO = os.path.join(RAIZ, "datos.json")
TZ = ZoneInfo("Europe/Madrid")
AHORA = dt.datetime.now(dt.timezone.utc)
HOY = AHORA.astimezone(TZ).date()
CLAVE = os.environ.get("AEMET_API_KEY", "").strip()
BASE = "https://opendata.aemet.es/opendata/api"
UA = {"User-Agent": "mapa-dana-ibiza (github actions)"}
BBOX = (38.60, 39.15, 1.15, 1.65)  # lat min, lat max, lon min, lon max
MUNICIPIOS = {  # id en la página -> código INE
    "eivissa": "07026", "santjosep": "07048", "santaeularia": "07054",
    "formentera": "07024", "santjoan": "07050", "santantoni": "07046",
}
URL_MUN = {
    "eivissa": "eivissa-id07026", "santjosep": "sant-josep-de-sa-talaia-id07048",
    "santaeularia": "santa-eularia-des-riu-id07054", "formentera": "formentera-san-francisco-javier-id07024",
    "santjoan": "sant-joan-de-labritja-id07050", "santantoni": "sant-antoni-de-portmany-id07046",
}
RSS = [
    ("Periódico de Ibiza", "https://www.periodicodeibiza.es/feed.html"),
    ("Noudiari", "https://www.noudiari.es/feed/"),
    ("La Voz de Ibiza", "https://lavozdeibiza.com/feed/"),
]
CLAVES_NOTICIA = re.compile(
    r"lluvi|dana|alerta|aviso|tormenta|torrent|inunda|carretera|cortad|reabiert|"
    r"emergenc|aemet|temporal|ume|es-alert|desprendim|riada|achique|meteobal", re.I)
NIVEL_ORDEN = {"verde": 0, "amarillo": 1, "naranja": 2, "rojo": 3}


def log(*a):
    print(*a, file=sys.stderr)


def get(url, decode=True, intentos=3):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            if not decode:
                return data
            for enc in ("utf-8", "iso-8859-15", "latin-1"):
                try:
                    return data.decode(enc)
                except UnicodeDecodeError:
                    continue
        except Exception as e:  # noqa: BLE001
            log(f"  fallo {url[:90]}: {e}")
            time.sleep(4 * (i + 1))
    return None


def aemet(ruta, decode=True):
    """Dos pasos: la API devuelve la URL de los datos."""
    if not CLAVE:
        return None
    sep = "&" if "?" in ruta else "?"
    txt = get(f"{BASE}{ruta}{sep}api_key={CLAVE}")
    if not txt:
        return None
    try:
        meta = json.loads(txt)
    except ValueError:
        log("  respuesta AEMET no es JSON")
        return None
    if meta.get("estado") != 200 or not meta.get("datos"):
        log(f"  AEMET {ruta}: {meta.get('estado')} {meta.get('descripcion')}")
        return None
    return get(meta["datos"], decode=decode)


def en_pitiusas(lat, lon):
    return BBOX[0] <= lat <= BBOX[1] and BBOX[2] <= lon <= BBOX[3]


def hora_local(texto):
    """'2026-10-19T19:00:00' (UTC) o con zona -> datetime local."""
    t = texto.replace("Z", "+00:00")
    m = re.match(r"(.*[+-]\d\d)(\d\d)$", t)
    if m:
        t = m.group(1) + ":" + m.group(2)
    d = dt.datetime.fromisoformat(t)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(TZ)


DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]


def etiqueta(d):
    return f"{DIAS[d.weekday()]} {d.day}, {d:%H:%M}"


def estaciones():
    txt = aemet("/observacion/convencional/todas")
    if not txt:
        return None
    try:
        filas = json.loads(txt)
    except ValueError:
        return None
    por_est = {}
    for f in filas:
        try:
            lat, lon = float(f["lat"]), float(f["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not en_pitiusas(lat, lon):
            continue
        por_est.setdefault(f["idema"], []).append(f)
    salida = []
    for idema, fs in por_est.items():
        fs.sort(key=lambda f: f.get("fint", ""))
        ult = fs[-1]
        try:
            t_ult = hora_local(ult["fint"])
        except (KeyError, ValueError):
            continue
        hoy = 0.0
        ultima_hora = None
        tiene_prec = False
        for f in fs:
            p = f.get("prec")
            if p is None:
                continue
            tiene_prec = True
            try:
                p = float(p)
            except (TypeError, ValueError):
                continue
            if hora_local(f["fint"]).date() == HOY:
                hoy += p
        if not tiene_prec:
            continue
        if ult.get("prec") is not None:
            try:
                ultima_hora = float(ult["prec"])
            except (TypeError, ValueError):
                ultima_hora = None
        nombre = (ult.get("ubi") or idema).strip().title()
        salida.append({
            "id": "aemet-" + idema.lower(),
            "n": f"{nombre} (AEMET)",
            "lat": round(float(ult["lat"]), 4),
            "lon": round(float(ult["lon"]), 4),
            "mm": round(hoy, 1),
            "h1": ultima_hora,
            "hora": etiqueta(t_ult),
            "url": "https://www.aemet.es/es/eltiempo/observacion/ultimosdatos?k=bal&l=" + idema,
            "approx": False,
        })
    salida.sort(key=lambda s: -s["mm"])
    log(f"  estaciones en Pitiusas: {len(salida)}")
    return salida or None


def avisos_aemet():
    """Nivel máximo vigente o próximo (24 h) en la zona Ibiza y Formentera."""
    data = aemet("/avisos_cap/ultimoelaborado/area/64", decode=False)
    if data is None:
        return None
    vistos = []
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data))
        archivos = [tf.extractfile(m).read() for m in tf.getmembers() if m.isfile()]
    except tarfile.TarError:
        archivos = [data]  # a veces es un XML suelto
    ns = {"c": "urn:oasis:names:tc:emergency:cap:1.2"}
    for raw in archivos:
        try:
            root = ET.fromstring(raw)
        except ET.ParseError:
            continue
        for info in root.findall("c:info", ns):
            lang = (info.findtext("c:language", "", ns) or "").lower()
            if lang and not lang.startswith("es"):
                continue
            zonas = " ".join(a.findtext("c:areaDesc", "", ns) for a in info.findall("c:area", ns))
            if "ibiza" not in zonas.lower() and "eivissa" not in zonas.lower():
                continue
            nivel = None
            for p in info.findall("c:parameter", ns):
                if "nivel" in (p.findtext("c:valueName", "", ns) or "").lower():
                    nivel = (p.findtext("c:value", "", ns) or "").strip().lower()
            if nivel not in NIVEL_ORDEN or nivel == "verde":
                continue
            try:
                ini = hora_local(info.findtext("c:onset", "", ns))
                fin = hora_local(info.findtext("c:expires", "", ns))
            except ValueError:
                continue
            if fin < AHORA or ini > AHORA + dt.timedelta(hours=24):
                continue
            evento = (info.findtext("c:event", "", ns) or "").strip()
            vistos.append((nivel, evento, ini, fin))
    if not vistos:
        return {"nivel": "verde", "titulo": "sin avisos",
                "texto": "AEMET no tiene avisos vigentes para Ibiza y Formentera en las próximas 24 horas."}
    maxn = max(vistos, key=lambda v: NIVEL_ORDEN[v[0]])[0]
    partes = []
    for nivel, evento, ini, fin in sorted(vistos, key=lambda v: v[2])[:4]:
        ev = re.sub(r"(?i)^aviso (de )?", "", evento)
        ev = re.sub(r"(?i)\s*de nivel \w+", "", ev).strip() or "Aviso"
        partes.append(f"{ev[:1].upper() + ev[1:]} ({nivel}) de {etiqueta(ini)} a {fin:%H:%M}")
    return {"nivel": maxn, "titulo": f"aviso {maxn}", "texto": ". ".join(partes) + "."}


def probabilidades(previas):
    if not CLAVE:
        return None
    previas = {p["id"]: p for p in previas or []}
    salida = []
    for pid, ine in MUNICIPIOS.items():
        txt = aemet(f"/prediccion/especifica/municipio/diaria/{ine}")
        dato = None
        try:
            dias = json.loads(txt)[0]["prediccion"]["dia"] if txt else []
        except (ValueError, KeyError, IndexError, TypeError):
            dias = []
        ahora_l = AHORA.astimezone(TZ)
        for dia in dias:
            try:
                fecha = dt.date.fromisoformat(dia["fecha"][:10])
            except (KeyError, ValueError):
                continue
            if fecha < HOY:
                continue
            franjas = {p.get("periodo", "00-24"): p.get("value") for p in dia.get("probPrecipitacion", [])}
            for per in ("00-06", "06-12", "12-18", "18-24", "00-12", "12-24", "00-24"):
                v = franjas.get(per)
                if v in (None, ""):
                    continue
                h_fin = int(per.split("-")[1])
                if fecha == HOY and h_fin <= ahora_l.hour:
                    continue
                dato = {"p": f"{int(v)}%", "note": f"{DIAS[fecha.weekday()]} {per.replace('-', '–')} h"}
                break
            if dato:
                break
        nuevo = dict(previas.get(pid, {"id": pid}))
        nuevo["id"] = pid
        nuevo["url"] = f"https://www.aemet.es/es/eltiempo/prediccion/municipios/{URL_MUN[pid]}"
        if dato:
            nuevo.update(dato)
        salida.append(nuevo)
        time.sleep(1.5)  # la API limita las peticiones por minuto
    return salida


def noticias():
    salida = []
    limite = AHORA - dt.timedelta(hours=48)
    for medio, url in RSS:
        txt = get(url)
        if not txt:
            continue
        try:
            root = ET.fromstring(txt.encode("utf-8"))
        except ET.ParseError:
            log(f"  RSS no válido: {medio}")
            continue
        for item in root.iter("item"):
            titulo = (item.findtext("title") or "").strip()
            enlace = (item.findtext("link") or "").strip()
            fecha = item.findtext("pubDate") or ""
            if not titulo or not enlace or not CLAVES_NOTICIA.search(titulo):
                continue
            try:
                f = email.utils.parsedate_to_datetime(fecha)
            except (TypeError, ValueError):
                continue
            if f.tzinfo is None:
                f = f.replace(tzinfo=dt.timezone.utc)
            if f < limite:
                continue
            salida.append({"ts": f.astimezone(TZ).strftime("%Y-%m-%dT%H:%M"),
                           "medio": medio, "titulo": titulo[:200], "url": enlace})
    vistos, unicas = set(), []
    for n in sorted(salida, key=lambda n: n["ts"], reverse=True):
        if n["url"] in vistos:
            continue
        vistos.add(n["url"])
        unicas.append(n)
    log(f"  titulares: {len(unicas)}")
    return unicas[:30] if unicas else None


def main():
    with open(ARCHIVO, encoding="utf-8") as fh:
        datos = json.load(fh)
    antes = json.dumps({k: v for k, v in datos.items() if k != "checkedAt"}, sort_keys=True, ensure_ascii=False)
    if not CLAVE:
        log("Sin AEMET_API_KEY: solo se actualizan los titulares de prensa.")

    est = estaciones()
    if est:
        manuales = [e for e in datos.get("estaciones", []) if not str(e.get("id", "")).startswith("aemet-")
                    and e.get("id") not in ("eivissa", "formentera")]
        datos["estaciones"] = est + manuales

    av = avisos_aemet()
    if av:
        datos.setdefault("estado", {})["aemet"] = av

    pr = probabilidades(datos.get("prob"))
    if pr:
        datos["prob"] = pr

    no = noticias()
    if no is not None:
        datos["noticias"] = no

    if est:
        lloviendo = [e for e in est if (e.get("h1") or 0) >= 0.2]
        if lloviendo:
            top = sorted(lloviendo, key=lambda e: -e["h1"])[:3]
            ahora_txt = "Lloviendo en la última hora: " + ", ".join(
                f"{e['n'].replace(' (AEMET)', '')} {e['h1']:g} l/m²" for e in top) + "."
        else:
            ahora_txt = "Ninguna estación de AEMET ha registrado lluvia en la última hora."
        maxhoy = est[0]
        datos.setdefault("estado", {})["notaAuto"] = (
            f"{ahora_txt} Hoy, el máximo es {maxhoy['mm']:g} l/m² en {maxhoy['n'].replace(' (AEMET)', '')} "
            f"(dato de las {maxhoy['hora'].split(', ')[-1]}).")

    despues = json.dumps({k: v for k, v in datos.items() if k not in ("checkedAt", "updatedAt")}, sort_keys=True, ensure_ascii=False)
    antes_sin = json.dumps({k: v for k, v in json.loads(antes).items() if k != "updatedAt"}, sort_keys=True, ensure_ascii=False)
    sello = AHORA.strftime("%Y-%m-%dT%H:%M:%SZ")
    if despues != antes_sin:
        datos["updatedAt"] = sello
        datos["checkedAt"] = sello
        texto = json.dumps(datos, ensure_ascii=False, indent=1) + "\n"
        json.loads(texto)
        with open(ARCHIVO, "w", encoding="utf-8") as fh:
            fh.write(texto)
        log("datos.json actualizado")
    else:
        log("sin cambios")


if __name__ == "__main__":
    main()
