// Ограничения целостности (Community Edition поддерживает только уникальность;
// обязательность полей — NOT NULL — валидируется на уровне загрузчиков, см. src/skg/models.py).

CREATE CONSTRAINT material_id IF NOT EXISTS
FOR (n:Material) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT experiment_id IF NOT EXISTS
FOR (n:Experiment) REQUIRE n.id IS UNIQUE;

// У режима нет естественного ключа: key — детерминированный хэш нормализованных
// параметров (type, t_min, t_max, duration, medium, params), считается загрузчиком.
// Одинаковые режимы из разных экспериментов схлопываются в один узел.
CREATE CONSTRAINT regime_key IF NOT EXISTS
FOR (n:Regime) REQUIRE n.key IS UNIQUE;

CREATE CONSTRAINT property_name IF NOT EXISTS
FOR (n:Property) REQUIRE n.name IS UNIQUE;

CREATE CONSTRAINT measurement_id IF NOT EXISTS
FOR (n:Measurement) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT equipment_id IF NOT EXISTS
FOR (n:Equipment) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT document_id IF NOT EXISTS
FOR (n:Document) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT person_id IF NOT EXISTS
FOR (n:Person) REQUIRE n.id IS UNIQUE;

CREATE CONSTRAINT conclusion_id IF NOT EXISTS
FOR (n:Conclusion) REQUIRE n.id IS UNIQUE;
