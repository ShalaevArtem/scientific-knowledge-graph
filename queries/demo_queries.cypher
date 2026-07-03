// Демонстрационные запросы по графу знаний (Neo4j Browser: http://localhost:7474).
// Работают после skg ingest + skg extract; п.5–6 — сразу после ingest.

// ─── 1. «Что делали по материалу X в процессе Y»: документы, где
//        со-упоминаются никель и электроэкстракция, с сущностями рядом ──
MATCH (m:Material {name: 'никель'})<-[:MENTIONS]-(d:Document)-[:MENTIONS]->(p:Process {name: 'электроэкстракция'})
OPTIONAL MATCH (d)-[:MENTIONS]->(e:Equipment)
RETURN d.title AS документ, d.category AS категория, d.year AS год, d.language AS язык,
       collect(DISTINCT e.name) AS оборудование
ORDER BY d.year DESC;

// ─── 2. Выводы по процессу с географией, уверенностью и источником ──────
MATCH (c:Conclusion)-[:ABOUT]->(p:Process)
WHERE p.name IN ['выщелачивание', 'кучное выщелачивание', 'электроэкстракция']
MATCH (c)-[di:DESCRIBED_IN]->(d:Document)
RETURN p.name AS процесс, c.text AS вывод, c.confidence AS уверенность,
       c.geography AS география, d.title AS источник, di.fragment AS фрагмент
ORDER BY p.name, c.confidence DESC;

// ─── 3. Числовые результаты с условиями: измерения из литературы ────────
MATCH (d:Document)-[rp:REPORTS]->(ms:Measurement)-[:OF_PROPERTY]->(pr:Property)
RETURN pr.name AS показатель, ms.value AS значение, ms.unit AS единица,
       ms.conditions AS условия, d.title AS источник, rp.unit AS место
LIMIT 25;

// ─── 4. Пробелы: верифицированные комбинации материал × процесс
//        без единого документа с со-упоминанием ─────────────────────────
MATCH (m:Material) WHERE m.needs_review IS NULL OR m.needs_review = false
MATCH (p:Process) WHERE p.needs_review IS NULL OR p.needs_review = false
WHERE NOT EXISTS {
  MATCH (d:Document)-[:MENTIONS]->(m) WHERE (d)-[:MENTIONS]->(p)
}
RETURN m.name AS материал, p.name AS процесс
ORDER BY материал LIMIT 50;

// ─── 5. Полнотекстовый поиск по фрагментам (русский анализатор) ─────────
CALL db.index.fulltext.queryNodes('chunk_search', 'обессоливание сульфаты')
YIELD node, score
MATCH (d:Document)-[:HAS_CHUNK]->(node)
RETURN d.title, node.unit_kind + ' ' + node.unit_no AS место,
       left(node.text, 200) AS фрагмент, score
ORDER BY score DESC LIMIT 10;

// ─── 6. Корпус по категориям и годам (после ingest) ─────────────────────
MATCH (d:Document)
RETURN d.category AS категория, count(*) AS документов,
       min(d.year) AS с_года, max(d.year) AS по_год,
       sum(CASE WHEN d.language = 'en' THEN 1 ELSE 0 END) AS англоязычных
ORDER BY документов DESC;

// ─── 7. Очередь на проверку эксперта: сущности, впервые встреченные
//        в текстах (не из seed-справочников) ────────────────────────────
MATCH (n) WHERE n.needs_review = true
RETURN labels(n)[0] AS тип, coalesce(n.name, n.id) AS имя, n.created_at AS создано
ORDER BY тип, имя LIMIT 50;

// ─── 8. Эксперты по теме: авторы документов, упоминающих процесс ────────
MATCH (p:Process {name: 'взвешенная плавка'})<-[:MENTIONS]-(d:Document)-[:AUTHORED_BY]->(a:Person)
RETURN a.name AS автор, count(DISTINCT d) AS документов,
       collect(DISTINCT d.title)[..5] AS работы
ORDER BY документов DESC;
