"""Точка входа отчёта по договорам (спецификация — DECISIONS.md).

Модели домена — models.py, бизнес-логика — services.py, вывод — cli.py.
Этот файл только запускает CLI: `python main.py [--date YYYY-MM-DD] [--json]`.
"""

from cli import main

if __name__ == "__main__":
    main()
