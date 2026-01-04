import streamlit as st
import json
import time
import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import gpxpy
import gpxpy.gpx
from simplification.cutil import simplify_coords
from garminconnect import Garmin

# --- CONFIGURAZIONE PAGINA & STILE ---
st.set_page_config(page_title="DIESSE - Garmin Exporter", page_icon="🚴‍♂️", layout="wide")

# CSS per personalizzare i colori (Arancione DIESSE e Dark Mode)
st.markdown("""
    <style>
    .stButton>button {
        background-color: #e67e22;
        color: white;
        border-radius: 8px;
        font-weight: bold;
    }
    .stButton>button:hover {
        background-color: #d35400;
        border: 1px solid #e67e22;
    }
    .stCodeBlock {
        border: 1px solid #e67e22 !important;
    }
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# --- LOGICA DI SICUREZZA (RANGE 3 GIORNI) ---
def _is_valid_diesse_code(user_input):
    try:
        valore_utente = int(user_input)
        tz_rome = pytz.timezone("Europe/Rome")
        now_rome = datetime.now(tz_rome)
        
        # Inizio Ieri (00:00:00)
        ieri = now_rome - timedelta(days=1)
        ts_inizio = int(tz_rome.localize(datetime(ieri.year, ieri.month, ieri.day, 0, 0, 0)).timestamp())
        
        # Inizio Domani (00:00:00) -> Il range copre tutto oggi fino alla mezzanotte di domani
        domani = now_rome + timedelta(days=1)
        ts_fine = int(tz_rome.localize(datetime(domani.year, domani.month, domani.day, 0, 0, 0)).timestamp())
        
        return ts_inizio <= valore_utente <= ts_fine
    except:
        return False

# --- FUNZIONI DI SUPPORTO ---
def update_log(message, log_placeholder):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    if 'log_lines' not in st.session_state:
        st.session_state.log_lines = []
    st.session_state.log_lines.append(line)
    log_placeholder.code("\n".join(st.session_state.log_lines))

def tcx_to_gpx_in_memory(tcx_bytes):
    try:
        text = tcx_bytes.decode("utf-8", errors="ignore")
        ns = {"tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}
        root = ET.fromstring(text)
        gpx = gpxpy.gpx.GPX()
        track = gpxpy.gpx.GPXTrack()
        gpx.tracks.append(track)
        seg = gpxpy.gpx.GPXTrackSegment()
        track.segments.append(seg)
        for tp in root.findall(".//tcx:Trackpoint", ns):
            pos = tp.find("tcx:Position", ns)
            if pos is not None:
                lat = float(pos.find("tcx:LatitudeDegrees", ns).text)
                lon = float(pos.find("tcx:LongitudeDegrees", ns).text)
                ele = tp.find("tcx:AltitudeMeters", ns)
                e = float(ele.text) if ele is not None else 0
                seg.points.append(gpxpy.gpx.GPXTrackPoint(lat, lon, elevation=e))
        return gpx.to_xml().encode("utf-8")
    except: return None

def extract_track_data(gpx_content, epsilon=0.00005):
    try:
        gpx = gpxpy.parse(gpx_content)
        if not gpx.tracks: return None, None, 0
        seg = gpx.tracks[0].segments[0]
        pts = [[p.latitude, p.longitude] for p in seg.points]
        elevs = [p.elevation or 0 for p in seg.points]
        pts_simple = simplify_coords(pts, epsilon)
        pts_final = [[round(p[0], 6), round(p[1], 6)] for p in pts_simple]
        dist_total = seg.length_3d() / 1000.0
        up, _ = seg.get_uphill_downhill()
        alti = []
        if len(elevs) > 1:
            for i, e in enumerate(elevs):
                d = round((i / (len(elevs)-1)) * dist_total, 2)
                alti.append([d, int(e)])
        return json.dumps(pts_final), json.dumps(alti), int(up)
    except: return None, None, 0

# --- INTERFACCIA UTENTE ---
if 'log_lines' not in st.session_state:
    st.session_state.log_lines = []

col_left, col_right = st.columns([1, 2])

with col_left:
    # Visualizzazione Logo DIESSE
    if os.path.exists("logo.jpg"):
        st.image("logo.jpg", use_container_width=True)
    else:
        st.title("DIESSE")
    
    st.markdown("### Accesso Sicuro")
    u_email = st.text_input("Email Garmin Connect")
    u_pass = st.text_input("Password", type="password")
    u_mfa = st.text_input("Codice MFA (se richiesto dal tuo account)")
    
    # Inserimento MANUALE del codice generato da MIT App Inventor
    u_code = st.text_input("Codice di conferma (Timestamp App)", help="Inserisci il numero calcolato sul tuo smartphone")
    
    st.divider()
    b_size = st.slider("Attività per blocco", 10, 100, 50)
    
    btn_start = st.button("🚀 AVVIA ESPORTAZIONE TOTALE", use_container_width=True)
    if st.button("Pulisci Log Console"):
        st.session_state.log_lines = []
        st.rerun()

with col_right:
    st.subheader("Console Log")
    # Finestra di log fissa con scorrimento (Height=600px ideale per PC e Mobile)
    with st.container(height=600, border=True):
        console_box = st.empty()
        console_box.code("\n".join(st.session_state.log_lines))

# --- PROCESSO DI LAVORO ---
if btn_start:
    if not _is_valid_diesse_code(u_code):
        st.error("Accesso Negato: il codice di conferma inserito manualmente non è valido.")
        update_log("❌ ERRORE: Codice di sicurezza errato.", console_box)
    elif not u_email or not u_pass:
        st.error("Inserire email e password Garmin.")
    else:
        try:
            st.session_state.log_lines = [] 
            update_log("Codice verificato. Avvio sessione Garmin...", console_box)
            
            client = Garmin(u_email, u_pass)
            if u_mfa:
                client.login(u_mfa)
            else:
                client.login()
            
            update_log("✅ Connessione stabilita. Inizio recupero dati...", console_box)
            
            all_data = []
            idx = 0
            while True:
                activities = client.get_activities(idx, b_size)
                if not activities: break
                
                for act in activities:
                    a_id = str(act.get("activityId"))
                    update_log(f"-> Analisi attività: {a_id}", console_box)
                    try:
                        raw = client.download_activity(a_id)
                        content = raw.encode("utf-8") if isinstance(raw, str) else raw
                        if b"TrainingCenterDatabase" in content:
                            content = tcx_to_gpx_in_memory(content)
                        
                        coords, alti, gain = extract_track_data(content)
                        if coords:
                            raw_date = act.get("startTimeGMT")
                            dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
                            all_data.append({
                                "NomeFile": a_id,
                                "TipoPercorso": act.get("activityType", {}).get("typeKey"),
                                "Data": int(dt.replace(tzinfo=pytz.UTC).timestamp()),
                                "Lunghezza": round(act.get("distance", 0) / 1000.0, 2),
                                "Dislivello": int(act.get("elevationGain", 0)) or gain,
                                "Durata": f"{int(act.get('duration')//3600)}h {int((act.get('duration')%3600)//60)}m",
                                "CoordLight": coords,
                                "Altimetria": alti
                            })
                    except: continue
                idx += b_size
                time.sleep(1)

            if all_data:
                all_data.sort(key=lambda x: x['Data'], reverse=True)
                json_out = json.dumps(all_data, indent=4, ensure_ascii=False)
                update_log(f"✅ Fatto! {len(all_data)} attività elaborate.", console_box)
                st.balloons()
                st.download_button("📥 SCARICA EXPORT_APP.JSON", json_out, "export_app.json", "application/json", use_container_width=True)
                
        except Exception as e:
            update_log(f"❌ ERRORE: {str(e)}", console_box)
            st.error("Si è verificato un errore durante l'esportazione.")