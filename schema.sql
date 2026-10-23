-- Пользователи
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    balance     INTEGER DEFAULT 0,
    subscribed  INTEGER DEFAULT 0,
    total_paid  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT
);

-- Чаты, где есть бот
CREATE TABLE IF NOT EXISTS chats (
    chat_id   INTEGER PRIMARY KEY,
    title     TEXT,
    added_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Заказы на пополнение
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    interval_min INTEGER NOT NULL,
    price        INTEGER NOT NULL,
    status       TEXT DEFAULT 'pending',
    confirmed_by INTEGER,
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    funded_at    TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Пиары
CREATE TABLE IF NOT EXISTS ads (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    text         TEXT NOT NULL,
    interval_min INTEGER NOT NULL,
    price        INTEGER NOT NULL,
    status       TEXT DEFAULT 'active',
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP,
    last_sent    TEXT,
    sent_count   INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- Роли
CREATE TABLE IF NOT EXISTS roles (
    user_id    INTEGER NOT NULL,
    role       TEXT NOT NULL,
    granted_by INTEGER,
    granted_at TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, role)
);

-- Индексы
CREATE INDEX IF NOT EXISTS idx_ads_status    ON ads(status);
CREATE INDEX IF NOT EXISTS idx_ads_user      ON ads(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_user   ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_roles_user    ON roles(user_id);
