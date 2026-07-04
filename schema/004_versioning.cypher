// Версионирование фактов: при изменении вывода (повторное извлечение новой
// моделью, обновлённый документ) прежнее состояние архивируется в ConclusionVersion,
// связанный с актуальным узлом ребром HAD_VERSION.

CREATE INDEX conclusionversion_conclusion IF NOT EXISTS
FOR (v:ConclusionVersion) ON (v.conclusion_id);

CREATE INDEX conclusionversion_archived IF NOT EXISTS
FOR (v:ConclusionVersion) ON (v.archived_at);
