{pkgs}: {
  deps = [
    pkgs.redis
    pkgs.postgresql
    pkgs.openssl
  ];
}
