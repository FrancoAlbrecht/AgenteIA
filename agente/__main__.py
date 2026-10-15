import importlib
import sys

SECTORES = ("impuestos", "auditoria", "laboral", "consultorias", "resumen_r2")


def main(argumentos):
    if len(argumentos) != 1 or argumentos[0] not in SECTORES:
        print(f"Uso: python -m agente <sector>\nSectores: {', '.join(SECTORES)}")
        return 2
    # Se importa sólo el sector pedido; su main() sale con código 1 si hubo error.
    importlib.import_module(f"agente.sectores.{argumentos[0]}").main()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
