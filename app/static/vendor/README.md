# Dependencias de terceros

- `chart.umd.js` — [Chart.js](https://www.chartjs.org/) v4.4.7, licencia MIT.
  Se incluye en el repo (en vez de cargarlo de un CDN) para que la app no
  dependa de una red externa ni de una CSP permisiva en produccion.
  Para actualizar: `npm pack chart.js@<version>` y copiar `dist/chart.umd.js`.
