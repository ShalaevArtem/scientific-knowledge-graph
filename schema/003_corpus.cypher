// Расширение схемы под корпус кейса (горно-металлургический домен):
// Process — таксономия процессов (выщелачивание, электроэкстракция…) с RU/EN-синонимами;
// Facility — предприятия/месторождения/проекты (география: страна → фильтр «РФ/мир»);
// TopicTag — тематические теги (гидрометаллургия, пирометаллургия, экология…);
// Chunk — фрагмент документа: единица семантического поиска и provenance.

CREATE CONSTRAINT process_name IF NOT EXISTS
FOR (n:Process) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT facility_name IF NOT EXISTS
FOR (n:Facility) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT topictag_name IF NOT EXISTS
FOR (n:TopicTag) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT chunk_id IF NOT EXISTS
FOR (n:Chunk) REQUIRE n.id IS UNIQUE;

// Фильтры по документам: категория (доклад/обзор/статья/журнал/конференция), год, язык
CREATE INDEX document_category IF NOT EXISTS
FOR (n:Document) ON (n.category);

CREATE INDEX document_year IF NOT EXISTS
FOR (n:Document) ON (n.year);

CREATE INDEX document_language IF NOT EXISTS
FOR (n:Document) ON (n.language);

CREATE INDEX facility_country IF NOT EXISTS
FOR (n:Facility) ON (n.country);

// Гибридный поиск по фрагментам: BM25 (русский анализатор) + вектор
CREATE FULLTEXT INDEX chunk_search IF NOT EXISTS
FOR (n:Chunk) ON EACH [n.text]
OPTIONS {indexConfig: {`fulltext.analyzer`: 'russian'}};

// Размерность 384 = intfloat/multilingual-e5-small (см. src/skg/config.py)
CREATE VECTOR INDEX chunk_embedding IF NOT EXISTS
FOR (n:Chunk) ON (n.embedding)
OPTIONS {indexConfig: {`vector.dimensions`: 384, `vector.similarity_function`: 'cosine'}};
