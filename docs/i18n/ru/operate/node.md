# Запуск ноды на Linux

Обычная нода Kairos проверяет полные блоки, поддерживает Live State,
ретранслирует транзакции и обслуживает синхронизацию. Она не майнит.

Это руководство устанавливает официальный релиз Core как системную службу на
64-битном сервере Linux с systemd.

## Требования

Штатная реализация системы доказательств требует:

- x86-64 с SSE4.1 и PCLMULQDQ; либо
- ARM64 с NEON и PMULL.

При запуске автоматически выбирается `pclmul`, `avx2+vpclmul`,
`avx512bw+vpclmul` или `neon+pmull`. Скалярная эталонная реализация в
штатной ноде не используется.

P2P-соединения принимаются на TCP `9700`. JSON-RPC должен оставаться на
`127.0.0.1:9701`.

Основной изменяемый объём хранилища определяется размером Live State.
Компактные заголовки по 212 байт хранятся постоянно, а полные тела блоков —
только для последних 42 блоков.

До заказа виртуальной машины и выбора лимитов прочитайте
[Оборудование и ресурсы](hardware.md).

## Установка релиза Core

Скачайте архив нужной архитектуры и `SHA256SUMS` со
[страницы релизов](https://github.com/proof-native/parano1d/releases). Проверьте
архив до распаковки, заменив `VERSION` номером версии:

```sh
grep '  kairos-core-vVERSION-linux-x86_64.tar.gz$' SHA256SUMS \
  | sha256sum --check
```

Команда должна вывести `OK`. Для ARM64 замените `linux-x86_64` на
`linux-aarch64`.

Распакуйте архив и проверьте оборудование:

```sh
tar -xzf kairos-core-vVERSION-linux-x86_64.tar.gz
./kairos --check-hardware
```

На поддерживаемой машине отчёт заканчивается:

```text
NODE READY
```

Установите ноду и CLI:

```sh
sudo install -m 0755 kairos kairos-cli /usr/local/bin/
```

## Системный пользователь

Храните данные ноды отдельно от интерактивных пользователей:

```sh
sudo useradd --system --home-dir /var/lib/kairos \
  --create-home --shell /usr/sbin/nologin kairos
sudo install -d -o kairos -g kairos -m 0700 /var/lib/kairos
sudo install -d -o root -g kairos -m 0750 /etc/kairos
```

Создайте `/etc/kairos/kairos.toml`:

```toml
[network]
listen = "0.0.0.0:9700"
seeds = []

[storage]
backend = "mdbx"
path = "/var/lib/kairos"

[rpc]
listen = "127.0.0.1:9701"

[mining]
enabled = false
miner_address = ""
```

Защитите конфигурацию:

```sh
sudo chown root:kairos /etc/kairos/kairos.toml
sudo chmod 0640 /etc/kairos/kairos.toml
```

Указывать сид вручную не нужно. Релизный бинарник находит публичную сеть через
встроенные DNS-сиды и запоминает успешные исходящие пиры.

## Запуск под systemd

Создайте `/etc/systemd/system/kairos.service`:

```ini
[Unit]
Description=Kairos node
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=kairos
Group=kairos
ExecStart=/usr/local/bin/kairos --config /etc/kairos/kairos.toml
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=45
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

`KillSignal=SIGINT` оставляет ноде время корректно закрыть сетевые службы и
сбросить MDBX.

Загрузите файл службы и запустите ноду:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now kairos
sudo systemctl status kairos
```

Следите за запуском и синхронизацией:

```sh
sudo journalctl -u kairos -f
```

## Проверка ноды

По умолчанию CLI подключается к локальному RPC:

```sh
kairos-cli status
kairos-cli peers
kairos-cli state
```

`status` должен показывать актуальную высоту, число в `peers` должно стать
ненулевым, а `state` сообщает аутентифицированные размеры Live State.

## Сетевой доступ

Разрешите входящий TCP `9700` в межсетевых экранах сервера и провайдера. За
NAT перенаправьте TCP `9700` на ноду. Синхронизация работает и при наличии только
исходящих соединений, но входящие пиры делают ноду полезной для сети.

Не публикуйте TCP `9701`. Для удалённого администрирования используйте
SSH-туннель или другой аутентифицированный приватный транспорт.

## Остановка и обновление

Перед заменой бинарников или копированием данных остановите службу:

```sh
sudo systemctl stop kairos
```

Установите проверенные новые бинарники и запустите:

```sh
sudo install -m 0755 kairos kairos-cli /usr/local/bin/
sudo systemctl start kairos
kairos-cli status
```

При обычном обновлении не удаляйте `/var/lib/kairos`. Если кошелёк ноды
получает средства, отдельно сохраните `/var/lib/kairos/wallet.key` и
защищайте его как приватный секрет.
