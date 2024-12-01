{pkgs}: {
  deps = [
    pkgs.python-launcher
    pkgs.redis
    pkgs.postgresql
    pkgs.openssl
  ];
}
