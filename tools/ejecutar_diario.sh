#!/usr/bin/env bash
# ejecutar_diario.sh — Paso de GitHub Actions que llama a main.py.
#
# Lee MODO (normal | prueba | sin-publicar; vacío = normal), FECHA (opcional) y REEL (si | no).
# La salida 2 de main.py significa "pendiente": no es un fallo, los datos llegarán
# para el siguiente disparo.
#
# PYTHON permite cambiar el intérprete, por ejemplo para probarlo en local.

set -u

args=()
case "${MODO:-normal}" in
  ""|normal) args+=(--push) ;;
  prueba) args+=(--push --prueba) ;;
  sin-publicar) args+=(--sin-publicar) ;;
  *) echo "::error::Modo desconocido: ${MODO}"; exit 1 ;;
esac

if [ -n "${FECHA:-}" ]; then
  args+=(--fecha "$FECHA")
fi

# REEL=no hace solo el carrusel (vacío = sí, que es lo que llega en los disparos programados)
if [ "${REEL:-si}" = "no" ]; then
  args+=(--sin-reel)
fi

"${PYTHON:-python}" main.py "${args[@]}"
codigo=$?

if [ "$codigo" -eq 2 ]; then
  echo "::notice::PVPC pendiente: lo intentará el siguiente disparo."
  exit 0
fi
exit "$codigo"
