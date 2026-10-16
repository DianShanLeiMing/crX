# Device adapters

BaseEmulatorController defines the contract. AdbBasedController owns shared screenshot/input/application I/O.
AdbController attaches to an existing device. MuMuEmulatorController adds installation discovery,
instance queries and selected-instance startup, using the actual reported loopback ADB port.
Construction must not launch an emulator. restart() boots/connects and launches the game.
Missing games must fail before changing display settings.
Check screenshot geometry after bringing the game to the foreground (launchers may force landscape).
All host processes use argument vectors with shell=False and finite timeouts.
Do not kill the global ADB server or other emulator instances; do not create/delete instances.
Retry policy belongs to the worker; adapter boot waits must remain bounded.
