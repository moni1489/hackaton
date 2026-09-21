/* Карта области: точки школ, окраска по статусу канала, спутниковая подложка. */
import { useCallback, useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import { fmtStamp, statusMeta } from './ui';

mapboxgl.accessToken =
  'pk.eyJ1IjoiYmVicnVzZDMyIiwiYSI6ImNtbXozZTEzZTA0M3oycG93M3R5NHBranQifQ.pc5OgxomRXUl5pRDVktXuA';

const STYLES = {
  points: 'mapbox://styles/mapbox/light-v11',
  satellite: 'mapbox://styles/mapbox/satellite-streets-v12',
};

const SRC = 'schools-src';

const toGeoJSON = (schools) => ({
  type: 'FeatureCollection',
  features: schools
    .filter((s) => s.lat && s.lng)
    .map((s) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lng, s.lat] },
      properties: {
        id: s.id, name: s.name, code: s.school_id_code, status: s.status,
        color: statusMeta(s.status).color,
        provider: s.provider, connection: s.connection_type,
        // Устаревший замер не выдаётся за текущий показатель.
        download: s.is_stale ? '—' : (s.current_download ?? 0),
        ping: s.is_stale ? '—' : (s.current_ping ?? 0),
        contract: s.contract_speed_down ?? 0, last: fmtStamp(s.last_measurement),
      },
    })),
});

export default function MapView({ schools, mode, onOpenSchool, onSelect, selectedId }) {
  const holder = useRef(null);
  const map = useRef(null);
  const popup = useRef(null);
  const schoolsRef = useRef(schools);
  const baseRef = useRef('light');
  const fittedRef = useRef(false);
  const openRef = useRef(onOpenSchool);
  const selectRef = useRef(onSelect);
  schoolsRef.current = schools;
  openRef.current = onOpenSchool;
  selectRef.current = onSelect;

  /** Кадрирование по фактическому расположению школ (с учётом плавающих карточек). */
  const fitToData = useCallback((instance, data) => {
    if (fittedRef.current || !data.features.length) return;
    // Жёстко ограничиваем bbox ВКО — не улетаем в Россию
    const VKO_BOUNDS = [[78.0, 47.0], [87.5, 51.5]];
    instance.fitBounds(VKO_BOUNDS, {
      padding: { top: 50, bottom: 80, left: 60, right: 60 },
      maxZoom: 8, duration: 600,
    });
    fittedRef.current = true;
  }, []);

  const paint = useCallback((instance, schoolList) => {
    const data = toGeoJSON(schoolList);
    if (instance.getSource(SRC)) {
      instance.getSource(SRC).setData(data);
      fitToData(instance, data);
      return;
    }
    instance.addSource(SRC, { type: 'geojson', data });
    fitToData(instance, data);

    instance.addLayer({
      id: 'schools-glow',
      type: 'circle',
      source: SRC,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 9, 12, 20],
        'circle-color': ['get', 'color'],
        'circle-opacity': 0.16,
      },
    });

    instance.addLayer({
      id: 'schools-dot',
      type: 'circle',
      source: SRC,
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 5, 4.5, 12, 8],
        'circle-color': ['get', 'color'],
        'circle-stroke-width': 2,
        'circle-stroke-color': '#fff',
      },
    });

    instance.on('mouseenter', 'schools-dot', () => {
      instance.getCanvas().style.cursor = 'pointer';
    });
    instance.on('mouseleave', 'schools-dot', () => {
      instance.getCanvas().style.cursor = '';
    });
    instance.on('click', 'schools-dot', (event) => {
      const props = event.features[0].properties;
      selectRef.current?.(Number(props.id));
      const meta = statusMeta(props.status);
      const node = document.createElement('div');
      node.className = 'map-pop';
      node.innerHTML = `
        <div class="map-pop-code">
          <span class="mono" style="font-size:11px;color:#8B95AB;font-weight:700">${props.code}</span>
          <span class="tag ${meta.key}">${props.status}</span>
        </div>
        <div class="map-pop-name">${props.name}</div>
        <div class="map-pop-grid">
          <div><span>Загрузка</span><b style="color:${meta.color}">${props.download}</b></div>
          <div><span>Договор</span><b>${props.contract}</b></div>
          <div><span>Задержка</span><b>${props.ping === '—' ? '—' : `${props.ping} мс`}</b></div>
          <div><span>Линия</span><b style="font-size:11px">${props.connection}</b></div>
          <div style="grid-column:1/-1"><span>Последний замер</span><b style="font-size:11px">${props.last}</b></div>
        </div>
        <button class="btn accent" style="margin-top:13px;width:100%">Карточка школы</button>`;
      node.querySelector('button').addEventListener('click', () => {
        openRef.current?.(Number(props.id));
        popup.current?.remove();
      });
      popup.current?.remove();
      popup.current = new mapboxgl.Popup({ offset: 14, closeButton: false, maxWidth: '280px' })
        .setLngLat(event.lngLat).setDOMContent(node).addTo(instance);
    });
  }, [fitToData]);

  // Инициализация
  useEffect(() => {
    if (map.current) return undefined;
    const instance = new mapboxgl.Map({
      container: holder.current,
      style: STYLES[mode] || STYLES.points,
      center: [82.6, 49.95],   // Усть-Каменогорск — центр ВКО
      zoom: 7.5,
      minZoom: 5,
      maxBounds: [[73.0, 44.0], [92.0, 55.0]], // не улетать за пределы региона
      attributionControl: false,
    });
    baseRef.current = mode === 'satellite' ? 'satellite' : 'light';
    instance.on('load', () => paint(instance, schoolsRef.current));
    map.current = instance;
    return () => { instance.remove(); map.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Обновление данных
  useEffect(() => {
    const instance = map.current;
    if (instance?.isStyleLoaded()) paint(instance, schools);
  }, [schools, paint]);

  // Смена подложки (обычная / спутник)
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;

    const applyPaint = () => {
      if (!instance.getLayer('schools-dot')) return;
      instance.setPaintProperty('schools-dot', 'circle-stroke-color',
        mode === 'satellite' ? '#0E1420' : '#ffffff');
    };

    const run = () => {
      const wantBase = mode === 'satellite' ? 'satellite' : 'light';
      if (baseRef.current !== wantBase) {
        baseRef.current = wantBase;
        // Смена стиля сбрасывает слои — восстанавливаем их после загрузки нового стиля.
        instance.once('style.load', () => {
          paint(instance, schoolsRef.current);
          applyPaint();
        });
        instance.setStyle(STYLES[mode]);
      } else {
        applyPaint();
      }
    };

    if (instance.isStyleLoaded()) run();
    else instance.once('load', run);
  }, [mode, paint]);

  // Перелёт к выбранной школе
  useEffect(() => {
    if (!selectedId || !map.current) return;
    const school = schools.find((s) => s.id === selectedId);
    if (school?.lat) map.current.flyTo({ center: [school.lng, school.lat], zoom: 11, speed: 1.2 });
  }, [selectedId, schools]);

  const zoom = (delta) => map.current?.zoomTo((map.current.getZoom() || 6) + delta, { duration: 300 });
  const reset = () => {
    fittedRef.current = false;
    if (map.current) fitToData(map.current, toGeoJSON(schools));
  };

  return (
    <>
      {/* inline-стиль: mapbox-gl.css задаёт своему контейнеру position: relative */}
      <div ref={holder} className="map-root" style={{ position: 'absolute', inset: 0 }} />
      <div className="float tr map-ctl">
        <button onClick={() => zoom(1)} title="Приблизить">+</button>
        <button onClick={() => zoom(-1)} title="Отдалить">−</button>
        <button onClick={reset} title="Вся область" style={{ fontSize: 13 }}>⤢</button>
      </div>
    </>
  );
}
