/* Карта области: тепловая карта качества связи, точки школ и спутниковая подложка. */
import { useCallback, useEffect, useRef } from 'react';
import mapboxgl from 'mapbox-gl';
import 'mapbox-gl/dist/mapbox-gl.css';
import { statusMeta } from './ui';

mapboxgl.accessToken =
  'pk.eyJ1IjoiYmVicnVzZDMyIiwiYSI6ImNtbXozZTEzZTA0M3oycG93M3R5NHBranQifQ.pc5OgxomRXUl5pRDVktXuA';

const STYLES = {
  heat: 'mapbox://styles/mapbox/light-v11',
  points: 'mapbox://styles/mapbox/light-v11',
  satellite: 'mapbox://styles/mapbox/satellite-streets-v12',
};

const SRC = 'schools-src';

/** Тяжесть состояния канала 0…1 — вес точки в тепловой карте. */
const severity = (school) => {
  const weight = statusMeta(school.status).weight;
  return [0.12, 0.55, 0.85, 1][weight];
};

const toGeoJSON = (schools) => ({
  type: 'FeatureCollection',
  features: schools
    .filter((s) => s.lat && s.lng)
    .map((s) => ({
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [s.lng, s.lat] },
      properties: {
        id: s.id, name: s.name, code: s.school_id_code, status: s.status,
        color: statusMeta(s.status).color, severity: severity(s),
        provider: s.provider, connection: s.connection_type,
        download: s.current_download ?? 0, ping: s.current_ping ?? 0,
        contract: s.contract_speed_down ?? 0,
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
    const bounds = new mapboxgl.LngLatBounds();
    data.features.forEach((feature) => bounds.extend(feature.geometry.coordinates));
    instance.fitBounds(bounds, {
      padding: { top: 60, bottom: 110, left: 390, right: 70 },
      maxZoom: 8.5, duration: 0,
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
      id: 'schools-heat',
      type: 'heatmap',
      source: SRC,
      paint: {
        'heatmap-weight': ['get', 'severity'],
        'heatmap-intensity': ['interpolate', ['linear'], ['zoom'], 5, 0.9, 12, 2.4],
        'heatmap-radius': ['interpolate', ['linear'], ['zoom'], 5, 26, 12, 62],
        'heatmap-opacity': 0.72,
        // Одноцветная плотность → тёплые тона деградации (последовательная шкала)
        'heatmap-color': [
          'interpolate', ['linear'], ['heatmap-density'],
          0, 'rgba(23,166,91,0)', 0.2, 'rgba(23,166,91,.55)', 0.42, 'rgba(143,203,63,.72)',
          0.64, 'rgba(228,150,42,.82)', 0.84, 'rgba(224,69,62,.88)', 1, 'rgba(176,32,38,.94)',
        ],
      },
    });

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
          <div><span>Задержка</span><b>${props.ping} мс</b></div>
          <div><span>Линия</span><b style="font-size:11px">${props.connection}</b></div>
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
      style: STYLES[mode] || STYLES.heat,
      center: [82.9, 49.4],
      zoom: 6.1,
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


  // Смена режима отображения (подложка + видимость слоёв)
  useEffect(() => {
    const instance = map.current;
    if (!instance) return;

    const applyVisibility = () => {
      if (!instance.getLayer('schools-heat')) return;
      instance.setLayoutProperty('schools-heat', 'visibility', mode === 'heat' ? 'visible' : 'none');
      instance.setLayoutProperty('schools-glow', 'visibility', mode === 'heat' ? 'none' : 'visible');
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
          applyVisibility();
        });
        instance.setStyle(STYLES[mode]);
      } else {
        applyVisibility();
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
