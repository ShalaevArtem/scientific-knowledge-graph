// Индексы под типовые запросы этапа 1:
// «материал X при режиме Y → эффект на свойство Z», история во времени, пробелы.

// Временная ось: история решений и эволюция подходов
CREATE INDEX experiment_date IF NOT EXISTS
FOR (n:Experiment) ON (n.date);

CREATE INDEX conclusion_date IF NOT EXISTS
FOR (n:Conclusion) ON (n.date);

CREATE INDEX document_date IF NOT EXISTS
FOR (n:Document) ON (n.date);

// Поиск режимов диапазонами, а не строкой
CREATE INDEX regime_type IF NOT EXISTS
FOR (n:Regime) ON (n.type);

CREATE INDEX regime_temp IF NOT EXISTS
FOR (n:Regime) ON (n.temp_min, n.temp_max);

// Фильтры по значениям измерений
CREATE INDEX measurement_value IF NOT EXISTS
FOR (n:Measurement) ON (n.value);

CREATE INDEX experiment_status IF NOT EXISTS
FOR (n:Experiment) ON (n.status);

// Полнотекстовые индексы (русский анализатор Lucene).
// material_search — поиск материала по марке и синонимам («ВТ6» ≡ «Ti-6Al-4V»);
// точная нормализация делается загрузчиком, индекс — для нечёткого поиска в UI.
CREATE FULLTEXT INDEX material_search IF NOT EXISTS
FOR (n:Material) ON EACH [n.grade, n.synonyms]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'russian'}};

CREATE FULLTEXT INDEX document_search IF NOT EXISTS
FOR (n:Document) ON EACH [n.title]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'russian'}};

CREATE FULLTEXT INDEX conclusion_search IF NOT EXISTS
FOR (n:Conclusion) ON EACH [n.text]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'russian'}};
