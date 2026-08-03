"""
DATOS LOGÍSTICOS — SCRIPT DE ACTUALIZACIÓN
==========================================
Ejecutar: py actualizar_datos_logisticos.py

Flujo:
  1. Descarga Maestra productos desde URL Odoo
  2. Descarga Stock x bodega desde URL Odoo
  3. Descarga Ventas detalle (últimos 30 días) desde URL Odoo
  4. Procesa y cruza los 3 archivos
  5. Genera datos_logisticos.json embebible en el HTML
  6. Genera datos_logisticos.html autocontenido (doble clic para abrir)
  7. Escribe log.txt

Programar en Task Scheduler:
  Programa:  py
  Argumentos: "C:\\ruta\\actualizar_datos_logisticos.py"
  Inicio en: C:\\ruta\\
"""

import os, re, io, json, shutil, math
import pandas as pd
import requests
from datetime import datetime, date

# =========================================================
# CONFIGURACIÓN — rutas y URLs
# =========================================================
# CARPETA: en tu PC usa la ruta local; en GitHub Actions (u otro servidor)
# usa la carpeta actual del repositorio. Se detecta con la variable de
# entorno CPEDL_ENTORNO=nube que definimos en GitHub Actions.
if os.environ.get("CPEDL_ENTORNO") == "nube":
    CARPETA = os.getcwd()          # raíz del repositorio en la nube
else:
    CARPETA = r"C:\Users\fgonzalez\Desktop\CLAUDE\DIB\GESTION INVENTARIOS\DATOS LOGISTICOS"

ARCHIVO_JSON = os.path.join(CARPETA, "datos_logisticos.json")
ARCHIVO_HTML = os.path.join(CARPETA, "datos_logisticos.html")
ARCHIVO_LOG  = os.path.join(CARPETA, "actualizar_log.txt")

# URLs de Odoo: primero busca en variables de entorno (GitHub Secrets, seguras),
# y si no están, usa los archivos .txt locales (tu PC). Así el mismo script
# funciona en ambos lados sin exponer las URLs en el repositorio.
URL_ENV = {
    "URL MAESTRA PRODUCTOS.txt": "ODOO_URL_MAESTRA",
    "URL STOCKXBODEGA.txt":      "ODOO_URL_STOCK",
    "URL VENTAS DETALLE.txt":    "ODOO_URL_VENTAS",
}

def leer_url(nombre_archivo):
    # 1) Variable de entorno (Secret) si existe
    env_var = URL_ENV.get(nombre_archivo)
    if env_var and os.environ.get(env_var):
        return os.environ[env_var].strip()
    # 2) Archivo .txt local (respaldo para tu PC)
    ruta = os.path.join(CARPETA, nombre_archivo)
    if not os.path.exists(ruta):
        return ""
    with open(ruta, encoding="utf-8") as f:
        return f.read().strip()

# =========================================================
# LOG
# =========================================================
_log = []

def log(msg, nivel="INFO"):
    ts  = datetime.now().strftime("%H:%M:%S")
    txt = f"[{ts}] [{nivel}] {msg}"
    print(txt)
    _log.append(txt)

def guardar_log():
    with open(ARCHIVO_LOG, "w", encoding="utf-8") as f:
        f.write(f"Ejecución: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("\n".join(_log))

# =========================================================
# DESCARGA
# =========================================================
def descargar(url, nombre):
    log(f"Descargando {nombre}...")
    try:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        # Detectar si es CSV o Excel por content-type o contenido
        ct = r.headers.get("Content-Type", "")
        if "csv" in ct or url.endswith(".csv"):
            content = r.content.decode("utf-8-sig")
            df = pd.read_csv(io.StringIO(content))
        else:
            df = pd.read_excel(io.BytesIO(r.content))
        df.columns = [str(c).strip() for c in df.columns]
        log(f"  {nombre}: {len(df):,} registros, {len(df.columns)} columnas")
        return df
    except Exception as e:
        log(f"ERROR descargando {nombre}: {e}", "ERROR")
        raise

# =========================================================
# UTILIDADES
# =========================================================
def n(v):
    try: return float(v)
    except: return 0.0

def s(v):
    return str(v).strip() if v is not None else ""

def nt(v):
    return s(v).upper()

def clean_prov(p):
    """Elimina código numérico al inicio: '100086-1 MERINOS (COB)' → 'MERINOS (COB)'"""
    return re.sub(r'^[\d\.\-]+\s*\n?\s*', '', s(p)).strip()

def parse_medida(med):
    """Extrae (ancho, largo) de '133X190' → (133, 190) donde ancho <= largo"""
    m = re.search(r'(\d{2,3})[Xx](\d{2,3})', s(med))
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return min(a, b), max(a, b)
    return None, None

def calcular_m2_desde_medida(med):
    w, l = parse_medida(med)
    if w and l:
        return round(w * l / 10000, 4)
    return 0.0

def calcular_volumen(tpk, largo, ancho, alto):
    """Calcula volumen en m³ según tipo de embalaje"""
    if tpk == "ROLLO" and largo > 0 and alto > 0:
        return round(math.pi * (alto / 200) ** 2 * (largo / 100), 6)
    elif largo > 0 and ancho > 0 and alto > 0:
        return round(largo * ancho * alto / 1e6, 6)
    return 0.0

def estado_logistico(peso, largo, ancho, alt, tpk, linea):
    """
    Determina completitud logística:
      completo = tiene suficientes dims para calcular volumen de transporte
      parcial  = tiene algo pero le falta info clave
      vacio    = sin ningún dato

    Reglas por tipo:
      ROLLO (o ALFOMBRAS): necesita Largo>0 Y Alt>0 Y Alt<50 (diámetro)
      CAJA/SACO:           necesita Largo>0 Y Ancho>0 Y Alto>0
      Sin tipo (otros):    necesita al menos Largo>0 Y Ancho>0
    """
    is_rollo = tpk == "ROLLO" or (linea == "ALFOMBRAS" and tpk not in ("CAJA","SACO"))
    is_caja  = tpk in ("CAJA", "SACO")

    if largo == 0 and ancho == 0 and alt == 0:
        return "vacio"

    if is_rollo:
        # Completo si tiene largo del rollo Y diámetro válido
        if largo > 0 and alt > 0 and alt < 50:
            return "completo"
        # Parcial si tiene largo pero sin diámetro
        if largo > 0:
            return "parcial"
        return "vacio"

    if is_caja:
        if largo > 0 and ancho > 0 and alt > 0:
            return "completo"
        if largo > 0 or ancho > 0:
            return "parcial"
        return "vacio"

    # Sin tipo masterpack (alfombras CDM individuales, textil sin clasificar, etc.)
    if largo > 0 and ancho > 0:
        return "completo"
    if largo > 0 or ancho > 0:
        return "parcial"
    return "vacio"

# =========================================================
# PASO 1: PROCESAR MAESTRA — formato compacto con pools de strings
# =========================================================
# Formato de cada registro (array):
# [ref, i_nom, i_lin, i_sub, i_med, i_prv, i_buy, i_tpk, i_mpk,
#  kg, lrg, anc, alt, cpk, pbpk, pepk, lpk, apk, hpk, est]
# Índices: 0=ref  1=nom  2=lin  3=sub  4=med  5=prv  6=buy
#          7=tpk  8=mpk  9=kg  10=lrg 11=anc 12=alt
#         13=cpk 14=pbpk 15=pepk 16=lpk 17=apk 18=hpk 19=est
TPK_LIST = ["", "CAJA", "ROLLO", "SACO"]
MPK_LIST = ["", "CARTON", "MIXTO", "PLASTICO", "YUTE"]
EST_LIST = ["vacio", "parcial", "completo"]

def procesar_maestra(df):
    log("Procesando maestra de productos...")

    COL_ID   = "ID (identificación)"
    COL_REF  = "Referencia interna"
    COL_NOM  = "Nombre producto"
    COL_LIN  = "Linea"
    COL_SUB  = "Subfamilia"
    COL_MED  = "Medida estandar"
    COL_PRV  = "Proveedores"
    COL_BUY  = "Comprador"
    COL_TPK  = "Tipo masterpack"
    COL_PESO = "Peso";  COL_LRG = "Largo cm"
    COL_ANC  = "Ancho cm"; COL_ALT = "Alto cm"
    COL_CPK  = "Capacidad masterpack"
    COL_PBPK = "Peso Bruto masterpack"
    COL_PEPK = "Peso envase masterpack"
    COL_MPK  = "Material masterpack"
    COL_LPK  = "Largo masterpack"
    COL_APK  = "Ancho masterpack"
    COL_HPK  = "Alto masterpack"

    # Pools de strings (índice → valor)
    pools = {"nom":[], "lin":[], "sub":[], "med":[], "prv":[], "buy":[]}
    idxs  = {"nom":{}, "lin":{}, "sub":{}, "med":{}, "prv":{}, "buy":{}}

    # Mapa ref → columnas que el HTML NO tiene en sus registros (para reexportar las 23 cols)
    # El HTML ya tiene: ref, nom, lin, sub, med, prv, buy, peso, dims, embalaje
    # Solo faltan estas 4 → mapa liviano (~1.5MB en vez de 6.5MB del orig completo)
    COL_MATPROD = "Material de producto"
    COL_CAT     = "Categoría de producto"
    COL_FAM     = "Familia"
    extra_map = {}

    def idx(pool, val):
        if val not in idxs[pool]:
            idxs[pool][val] = len(pools[pool])
            pools[pool].append(val)
        return idxs[pool][val]

    registros = []
    completos = parciales = vacios = 0

    for _, r in df.iterrows():
        ref = s(r.get(COL_REF, ""))
        if not ref or ref == "nan": continue

        # Guardar columnas originales que el HTML no tiene (ID, Material prod, Categoría, Familia)
        extra_map[ref] = {
            "id":  s(r.get(COL_ID, "")),
            "mat": "" if pd.isna(r.get(COL_MATPROD)) else s(r.get(COL_MATPROD, "")),
            "cat": "" if pd.isna(r.get(COL_CAT)) else s(r.get(COL_CAT, "")),
            "fam": "" if pd.isna(r.get(COL_FAM)) else s(r.get(COL_FAM, "")),
        }

        nom  = s(r.get(COL_NOM, ""))
        lin  = s(r.get(COL_LIN, ""))
        sub  = s(r.get(COL_SUB, ""))
        med  = s(r.get(COL_MED, ""))
        prv  = clean_prov(r.get(COL_PRV, ""))
        buy  = s(r.get(COL_BUY, ""))
        tpk  = nt(r.get(COL_TPK, ""))
        mpk  = nt(r.get(COL_MPK, ""))
        kg   = round(n(r.get(COL_PESO, 0)), 3)
        lrg  = round(n(r.get(COL_LRG, 0)), 1)
        anc  = round(n(r.get(COL_ANC, 0)), 1)
        alt  = round(n(r.get(COL_ALT, 0)), 1)
        cpk  = round(n(r.get(COL_CPK, 0)), 1)
        pbpk = round(n(r.get(COL_PBPK, 0)), 3)
        pepk = round(n(r.get(COL_PEPK, 0)), 3)
        lpk  = round(n(r.get(COL_LPK, 0)), 1)
        apk  = round(n(r.get(COL_APK, 0)), 1)
        hpk  = round(n(r.get(COL_HPK, 0)), 1)

        # M2 desde medida estándar para alfombras
        if lin == "ALFOMBRAS" and med and med not in ("SIN DATO", "nan", ""):
            m2c = calcular_m2_desde_medida(med)

        est  = estado_logistico(kg, lrg, anc, alt, tpk, lin)
        if est == "completo":   completos += 1
        elif est == "parcial":  parciales += 1
        else:                   vacios    += 1

        ti = TPK_LIST.index(tpk) if tpk in TPK_LIST else 0
        xi = MPK_LIST.index(mpk) if mpk in MPK_LIST else 0
        ei = EST_LIST.index(est) if est in EST_LIST else 0

        registros.append([
            ref,
            idx("nom", nom), idx("lin", lin), idx("sub", sub),
            idx("med", med), idx("prv", prv),  idx("buy", buy),
            ti, xi,
            kg, lrg, anc, alt, cpk, pbpk, pepk, lpk, apk, hpk,
            ei
        ])

    log(f"  Total: {len(registros):,} | Completos: {completos:,} | Parciales: {parciales:,} | Vacíos: {vacios:,}")
    return registros, pools, extra_map

# =========================================================
# PASO 2: PROCESAR STOCK
# =========================================================
def procesar_stock(df):
    log("Procesando stock x bodega...")
    # Detectar columna de referencia
    ref_col = next((c for c in df.columns if "referencia" in c.lower()), None)
    bod_col = next((c for c in df.columns if "bodega" in c.lower()), None)
    qty_col = next((c for c in df.columns if "stock total" in c.lower() or "stock" == c.lower()), None)
    un_col  = next((c for c in df.columns if c.strip().lower() == "un"), None)

    if not ref_col:
        log("AVISO: No se encontró columna de referencia en stock", "WARN")
        return {}, []

    stock_map = {}
    bodegas   = set()
    for _, r in df.iterrows():
        ref = s(r.get(ref_col, ""))
        if not ref or ref == "nan":
            continue
        bod = s(r.get(bod_col, "")) if bod_col else ""
        qty = n(r.get(qty_col, 0)) if qty_col else 0
        un  = s(r.get(un_col, "")) if un_col else ""
        if ref not in stock_map:
            stock_map[ref] = []
        stock_map[ref].append({"bodega": bod, "qty": qty, "un": un})
        if bod:
            bodegas.add(bod)

    log(f"  Referencias con stock: {len(stock_map):,} | Bodegas: {len(bodegas)}")
    return stock_map, sorted(bodegas)

# =========================================================
# PASO 3: PROCESAR VENTAS
# =========================================================
def procesar_ventas(df):
    log("Procesando ventas 30 días...")
    # Detectar columna de código
    cod_col = next((c for c in df.columns if c.strip().lower() in ("codigo", "código")), None)
    qty_col = next((c for c in df.columns if c.strip().lower() == "cantidad"), None)
    vnt_col = next((c for c in df.columns if "total venta" in c.strip().lower()), None)

    if not cod_col:
        log("AVISO: No se encontró columna Codigo en ventas", "WARN")
        return {}

    ventas_map = {}
    for _, r in df.iterrows():
        ref = s(r.get(cod_col, ""))
        if not ref or ref == "nan":
            continue
        qty = n(r.get(qty_col, 0)) if qty_col else 0
        vnt = n(r.get(vnt_col, 0)) if vnt_col else 0
        if ref not in ventas_map:
            ventas_map[ref] = {"qty": 0, "total": 0, "trans": 0}
        ventas_map[ref]["qty"]   += qty
        ventas_map[ref]["total"] += vnt
        ventas_map[ref]["trans"] += 1

    con_ventas = len(ventas_map)
    log(f"  Referencias con ventas: {con_ventas:,}")
    return ventas_map

# =========================================================
# PASO 4: CONSTRUIR STATS DE GRUPO
# =========================================================
def construir_stats_grupo(registros, pools):
    log("Construyendo estadísticas de grupos (subfamilia + medida)...")
    subs = pools["sub"]; meds = pools["med"]
    grupos = {}
    for r in registros:
        sub = subs[r[3]] if r[3] < len(subs) else ""
        med = meds[r[4]] if r[4] < len(meds) else ""
        k   = f"{sub}||{med}"
        if k not in grupos:
            grupos[k] = {"n": 0, "ok": 0, "kgs": [], "alts": [], "lrgs": []}
        g = grupos[k]
        g["n"] += 1
        if r[19] == 2: g["ok"] += 1          # est=completo
        if r[9]  > 0:  g["kgs"].append(r[9]) # kg
        if r[12] > 0 and r[12] < 50: g["alts"].append(r[12])  # alt
        if r[10] > 0:  g["lrgs"].append(r[10])  # lrg

    # Calcular promedios y desviaciones
    stats = {}
    for k, g in grupos.items():
        def avg(lst): return round(sum(lst)/len(lst), 3) if lst else 0
        def std(lst):
            if len(lst) < 2: return 0
            m = sum(lst)/len(lst)
            return round((sum((x-m)**2 for x in lst)/len(lst))**0.5, 3)
        stats[k] = {
            "n":   g["n"],
            "ok":  g["ok"],
            "kg":  avg(g["kgs"]),
            "kgsd": std(g["kgs"]),
            "alt": avg(g["alts"]),
            "lrg": avg(g["lrgs"]),
        }

    log(f"  Grupos únicos: {len(stats):,}")
    return stats

# =========================================================
# PASO 5: CONSTRUIR SUGERENCIAS DE DIÁMETRO
# =========================================================
def construir_diams(registros, pools):
    """Para cada ancho de alfombra, los diámetros más comunes."""
    lins = pools["lin"]
    diam_map = {}
    for r in registros:
        lin = lins[r[2]] if r[2] < len(lins) else ""
        tpk = TPK_LIST[r[7]] if r[7] < len(TPK_LIST) else ""
        if tpk != "ROLLO" and lin != "ALFOMBRAS": continue
        w = int(round(r[10])) if r[10] > 0 else 0   # lrg
        d = int(round(r[12])) if r[12] > 0 and r[12] < 50 else 0  # alt
        if w > 0 and d > 0:
            if w not in diam_map: diam_map[w] = {}
            diam_map[w][d] = diam_map[w].get(d, 0) + 1
    return {
        str(w): sorted(v.items(), key=lambda x: -x[1])[:3]
        for w, v in diam_map.items()
    }

# =========================================================
# PASO 6: GENERAR JSON
# =========================================================
def generar_json(registros, pools, stock_map, ventas_map, stats_grp, diam_sugs, bodegas, extra_map):
    log("Generando datos_logisticos.json...")
    n_comp = sum(1 for r in registros if r[19] == 2)  # est index 2 = completo
    n_parc = sum(1 for r in registros if r[19] == 1)
    n_vac  = sum(1 for r in registros if r[19] == 0)
    data = {
        "generado": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "totales": {
            "productos": len(registros),
            "completos": n_comp,
            "parciales": n_parc,
            "vacios":    n_vac,
        },
        "bodegas":  bodegas,
        "pools":    pools,
        "recs":     registros,
        "stock":    stock_map,
        "ventas":   ventas_map,
        "grp":      stats_grp,
        "diam":     diam_sugs,
        "extra":    extra_map,
    }
    with open(ARCHIVO_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    size_kb = os.path.getsize(ARCHIVO_JSON) // 1024
    log(f"  JSON generado: {ARCHIVO_JSON} ({size_kb} KB)")
    return data

# =========================================================
# PASO 7: GENERAR HTML AUTOCONTENIDO
# =========================================================
def generar_html(data):
    log("Generando datos_logisticos.html...")

    # Template: datos_logisticos_template.html (nunca se modifica)
    # Salida:    datos_logisticos.html (se regenera cada vez - este es el que abres)
    template_path = os.path.join(CARPETA, "datos_logisticos_template.html")
    if not os.path.exists(template_path):
        log("ERROR: datos_logisticos_template.html no encontrado en carpeta.", "ERROR")
        log(f"  Colócalo en: {CARPETA}", "ERROR")
        return

    with open(template_path, encoding="utf-8") as f:
        html = f.read()

    # Inyectar datos como variable JS
    data_js = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    inject  = f"const DATA = {data_js};"

    # Reemplaza la línea completa para evitar declaración duplicada de DATA
    if "const DATA = null; /* __DATA_INJECT__ */" in html:
        html = html.replace("const DATA = null; /* __DATA_INJECT__ */", inject)
    elif "const DATA = null;" in html:
        html = html.replace("const DATA = null;", inject)
    else:
        log("AVISO: marcador de datos no encontrado en template.", "WARN")
        return

    # Sobrescribe datos_logisticos.html — este es el archivo que usas
    output = os.path.join(CARPETA, "datos_logisticos.html")
    with open(output, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = os.path.getsize(output) // 1024
    log(f"  HTML generado: {output} ({size_kb} KB)")

    # En la nube, GitHub Pages sirve 'index.html' como página principal.
    # Generamos una copia con ese nombre para que la URL muestre la herramienta.
    if os.environ.get("CPEDL_ENTORNO") == "nube":
        index_out = os.path.join(CARPETA, "index.html")
        with open(index_out, "w", encoding="utf-8") as f:
            f.write(html)
        log(f"  index.html generado para GitHub Pages")
    else:
        log(f"  → Abre datos_logisticos.html con doble clic (o desde Google Drive)")

# =========================================================
# MAIN
# =========================================================
def main():
    log("=" * 55)
    log("DATOS LOGÍSTICOS — ACTUALIZACIÓN")
    log(f"Ejecutando: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 55)

    url_maestra = leer_url("URL MAESTRA PRODUCTOS.txt")
    url_stock   = leer_url("URL STOCKXBODEGA.txt")
    url_ventas  = leer_url("URL VENTAS DETALLE.txt")

    if not url_maestra:
        log("ERROR: No se encontró URL MAESTRA PRODUCTOS.txt", "ERROR")
        guardar_log()
        return

    try:
        # Descargas
        df_mae = descargar(url_maestra, "Maestra productos")
        df_stk = descargar(url_stock,   "Stock x bodega")  if url_stock  else pd.DataFrame()
        df_vnt = descargar(url_ventas,  "Ventas 30 días")  if url_ventas else pd.DataFrame()

        # Procesamiento
        log("=" * 55)
        registros, pools, extra_map = procesar_maestra(df_mae)
        stock_map, bodegas  = procesar_stock(df_stk)   if len(df_stk)  else ({}, [])
        ventas_map          = procesar_ventas(df_vnt)   if len(df_vnt)  else {}
        stats_grp           = construir_stats_grupo(registros, pools)
        diam_sugs           = construir_diams(registros, pools)

        # Salidas
        log("=" * 55)
        data = generar_json(registros, pools, stock_map, ventas_map, stats_grp, diam_sugs, bodegas, extra_map)
        generar_html(data)

        log("=" * 55)
        log(f"COMPLETADO — {len(registros):,} productos procesados")
        tot = data['totales']
        pct = round(tot['completos'] / tot['productos'] * 100, 1) if tot['productos'] else 0
        log(f"Completos: {tot['completos']:,} ({pct}%) | Parciales: {tot['parciales']:,} | Vacíos: {tot['vacios']:,}")

    except Exception as e:
        log(f"ERROR FATAL: {e}", "ERROR")
        import traceback
        log(traceback.format_exc(), "ERROR")

    finally:
        guardar_log()
        log(f"Log guardado en: {ARCHIVO_LOG}")

if __name__ == "__main__":
    main()
