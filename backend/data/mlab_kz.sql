-- Standard SQL. End date exclusive; aggregate only, no IP addresses.
-- Schema: https://www.measurementlab.net/tests/ndt/views/migrate/
SELECT CAST(date AS STRING) AS day,
       client.Geo.CountryCode AS country_code,
       COALESCE(client.Geo.City, 'Unknown') AS city,
       CAST(client.Network.ASNumber AS STRING) AS asn,
       COUNT(*) AS tests,
       APPROX_QUANTILES(a.MeanThroughputMbps, 100)[OFFSET(50)] AS download_mbps,
       APPROX_QUANTILES(a.MinRTT, 100)[OFFSET(50)] AS rtt_ms
FROM `measurement-lab.ndt.unified_downloads`
WHERE date >= @start_date AND date < @end_date
  AND client.Geo.CountryCode = 'KZ'
GROUP BY day, country_code, city, asn
ORDER BY day, city, asn
