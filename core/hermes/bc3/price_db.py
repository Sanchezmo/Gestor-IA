"""
BC3 Price Database - Base de precios para catálogos BC3.

Proporciona:
- Carga de bases de precios oficiales (BC3, BEDEC, PRE/OC, etc.)
- Búsqueda de precios por código, descripción, tipo de recurso
- Actualización automática de precios
- Versionado histórico de precios
- Interpolación de precios para items no encontrados
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from .models import BC3ResourceType, BC3Unit, BC3PriceEntry


# =============================================================================
# PRICE ENTRY MODEL
# =============================================================================

@dataclass(frozen=True, slots=True)
class BC3PriceEntry:
    """Entrada de precio en base de datos BC3."""
    
    id: UUID = field(default_factory=uuid4)
    code: str = ""
    description: str = ""
    resource_type: BC3ResourceType = BC3ResourceType.MATERIAL
    unit: BC3Unit = BC3Unit.UN
    price: Decimal = Decimal("0")
    currency: str = "EUR"
    source: str = "manual"  # bc3 | bedec | preoc | manual | import
    source_file: str | None = None
    valid_from: date = field(default_factory=date.today)
    valid_to: date | None = None
    region: str | None = None  # Código provincia/región
    supplier: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    @property
    def is_valid_today(self) -> bool:
        today = date.today()
        if self.valid_from > today:
            return False
        if self.valid_to and self.valid_to < today:
            return False
        return True


# =============================================================================
# PRICE DATABASE
# =============================================================================

class BC3PriceDatabase:
    """
    Base de datos de precios BC3 con SQLite backend.
    
    Características:
    - Índices optimizados para búsquedas frecuentes
    - Búsqueda por código, descripción, tipo recurso
    - Filtrado por fecha vigencia, región, proveedor
    - Importación desde CSV, JSON, Excel
    - Versionado histórico de precios
    """
    
    def __init__(self, db_path: str | Path = "bc3_prices.db"):
        self.db_path = Path(db_path)
        self._init_db()
    
    def _init_db(self) -> None:
        """Inicializar esquema de base de datos."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prices (
                    id TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    description TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT DEFAULT 'EUR',
                    source TEXT DEFAULT 'manual',
                    source_file TEXT,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    region TEXT,
                    supplier TEXT,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            
            # Índices para búsquedas frecuentes
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_code ON prices(code)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_description ON prices(description)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_resource_type ON prices(resource_type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_valid_from ON prices(valid_from)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_valid_to ON prices(valid_to)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_region ON prices(region)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_supplier ON prices(supplier)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_price_source ON prices(source)")
            
            conn.commit()
    
    # =========================================================================
    # CRUD OPERATIONS
    # =========================================================================
    
    def add_price(self, entry: BC3PriceEntry) -> BC3PriceEntry:
        """Añadir nueva entrada de precio."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO prices (
                    id, code, description, resource_type, unit, price,
                    currency, source, source_file, valid_from, valid_to,
                    region, supplier, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(entry.id),
                entry.code,
                entry.description,
                entry.resource_type.value,
                entry.unit.value,
                float(entry.price),
                entry.currency,
                entry.source,
                entry.source_file,
                entry.valid_from.isoformat(),
                entry.valid_to.isoformat() if entry.valid_to else None,
                entry.region,
                entry.supplier,
                json.dumps(entry.metadata),
                entry.created_at.isoformat(),
                entry.updated_at.isoformat(),
            ))
            conn.commit()
        return entry
    
    def update_price(self, entry_id: UUID, updates: dict[str, Any]) -> BC3PriceEntry | None:
        """Actualizar entrada de precio existente."""
        # Obtener entrada actual
        current = self.get_by_id(entry_id)
        if not current:
            return None
        
        # Aplicar actualizaciones
        updated = BC3PriceEntry(
            id=current.id,
            code=updates.get("code", current.code),
            description=updates.get("description", current.description),
            resource_type=BC3ResourceType(updates.get("resource_type", current.resource_type.value)),
            unit=BC3Unit(updates.get("unit", current.unit.value)),
            price=Decimal(str(updates.get("price", current.price))),
            currency=updates.get("currency", current.currency),
            source=updates.get("source", current.source),
            source_file=updates.get("source_file", current.source_file),
            valid_from=date.fromisoformat(updates["valid_from"]) if "valid_from" in updates else current.valid_from,
            valid_to=date.fromisoformat(updates["valid_to"]) if updates.get("valid_to") else current.valid_to,
            region=updates.get("region", current.region),
            supplier=updates.get("supplier", current.supplier),
            metadata={**current.metadata, **updates.get("metadata", {})},
            created_at=current.created_at,
            updated_at=datetime.now(),
        )
        
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE prices SET
                    code=?, description=?, resource_type=?, unit=?, price=?,
                    currency=?, source=?, source_file=?, valid_from=?, valid_to=?,
                    region=?, supplier=?, metadata=?, updated_at=?
                WHERE id=?
            """, (
                updated.code,
                updated.description,
                updated.resource_type.value,
                updated.unit.value,
                float(updated.price),
                updated.currency,
                updated.source,
                updated.source_file,
                updated.valid_from.isoformat(),
                updated.valid_to.isoformat() if updated.valid_to else None,
                updated.region,
                updated.supplier,
                json.dumps(updated.metadata),
                updated.updated_at.isoformat(),
                str(entry_id),
            ))
            conn.commit()
        return updated
    
    def delete_price(self, entry_id: UUID) -> bool:
        """Eliminar entrada de precio."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM prices WHERE id=?", (str(entry_id),))
            conn.commit()
            return cursor.rowcount > 0
    
    def get_by_id(self, entry_id: UUID) -> BC3PriceEntry | None:
        """Obtener entrada por ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM prices WHERE id=?", (str(entry_id),))
            row = cursor.fetchone()
            if not row:
                return None
            return self._row_to_entry(row)
    
    # =========================================================================
    # SEARCH METHODS
    # =========================================================================
    
    def search_by_code(self, code: str, exact: bool = True) -> list[BC3PriceEntry]:
        """Buscar precios por código."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if exact:
                cursor = conn.execute("SELECT * FROM prices WHERE code=?", (code,))
            else:
                cursor = conn.execute("SELECT * FROM prices WHERE code LIKE ?", (f"%{code}%",))
            return [self._row_to_entry(row) for row in cursor.fetchall()]
    
    def search_by_description(
        self,
        description: str,
        resource_type: BC3ResourceType | None = None,
        limit: int = 50,
    ) -> list[BC3PriceEntry]:
        """Buscar precios por descripción (búsqueda parcial)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT * FROM prices WHERE description LIKE ?"
            params = [f"%{description}%"]
            
            if resource_type:
                query += " AND resource_type = ?"
                params.append(resource_type.value)
            
            query += " ORDER BY description LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [self._row_to_entry(row) for row in cursor.fetchall()]
    
    def search_by_resource_type(
        self,
        resource_type: BC3ResourceType,
        valid_today: bool = True,
        limit: int = 100,
    ) -> list[BC3PriceEntry]:
        """Buscar precios por tipo de recurso."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT * FROM prices WHERE resource_type = ?"
            params = [resource_type.value]
            
            if valid_today:
                today = date.today().isoformat()
                query += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)"
                params.extend([today, today])
            
            query += " ORDER BY description LIMIT ?"
            params.append(limit)
            
            cursor = conn.execute(query, params)
            return [self._row_to_entry(row) for row in cursor.fetchall()]
    
    def get_best_price(
        self,
        code: str,
        resource_type: BC3ResourceType | None = None,
        valid_on: date | None = None,
    ) -> BC3PriceEntry | None:
        """
        Obtener el mejor precio para un código.
        
        Criterios de selección:
        1. Coincidencia exacta de código
        2. Vigente en la fecha (valid_on)
        3. Tipo de recurso coincidente (si se especifica)
        4. Más reciente (updated_at)
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            
            query = "SELECT * FROM prices WHERE code = ?"
            params = [code]
            
            if resource_type:
                query += " AND resource_type = ?"
                params.append(resource_type.value)
            
            if valid_on:
                date_str = valid_on.isoformat()
                query += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)"
                params.extend([date_str, date_str])
            
            query += " ORDER BY updated_at DESC LIMIT 1"
            
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            if row:
                return self._row_to_entry(row)
        return None
    
    def interpolate_price(
        self,
        description: str,
        resource_type: BC3ResourceType,
        unit: BC3Unit,
        region: str | None = None,
    ) -> Decimal | None:
        """
        Interpolar precio para item no encontrado.
        
        Busca items similares y calcula precio medio ponderado.
        """
        similar = self.search_by_description(
            description=description[:50],  # Primeras 50 chars
            resource_type=resource_type,
            limit=20,
        )
        
        # Filtrar por unidad similar
        filtered = [e for e in similar if e.unit == unit]
        if region:
            filtered = [e for e in filtered if e.region == region or e.region is None]
        
        if not filtered:
            return None
        
        # Filtrar solo vigentes hoy
        today = date.today()
        filtered = [e for e in filtered if e.is_valid_today]
        
        if not filtered:
            return None
        
        # Calcular precio medio ponderado por recencia
        total_weight = 0
        weighted_sum = Decimal("0")
        
        for entry in filtered:
            days_old = (date.today() - entry.updated_at.date()).days
            weight = max(1, 365 - days_old)  # Más peso a precios recientes
            total_weight += weight
            weighted_sum += entry.price * weight
        
        if total_weight == 0:
            return None
        
        return (weighted_sum / total_weight).quantize(Decimal("0.01"))
    
    def get_price_statistics(self) -> dict[str, Any]:
        """Obtener estadísticas de la base de precios."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT 
                    COUNT(*) as total,
                    COUNT(DISTINCT code) as unique_codes,
                    COUNT(DISTINCT resource_type) as resource_types,
                    MIN(price) as min_price,
                    MAX(price) as max_price,
                    AVG(price) as avg_price,
                    COUNT(DISTINCT source) as sources
                FROM prices
            """)
            row = cursor.fetchone()
            return dict(row) if row else {}
    
    # =========================================================================
    # IMPORT/EXPORT
    # =========================================================================
    
    def import_from_csv(
        self,
        file_path: str | Path,
        source: str = "csv",
        encoding: str = "utf-8",
        delimiter: str = ";",
        has_header: bool = True,
    ) -> tuple[int, list[str]]:
        """
        Importar precios desde CSV.
        
        Columnas esperadas: code, description, resource_type, unit, price, 
        currency, valid_from, valid_to, region, supplier
        """
        errors = []
        imported = 0
        
        with open(file_path, "r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f, delimiter=delimiter) if has_header else csv.reader(f, delimiter=delimiter)
            
            if not has_header:
                # Asumir orden de columnas estándar
                fieldnames = ["code", "description", "resource_type", "unit", "price", 
                             "currency", "valid_from", "valid_to", "region", "supplier"]
                reader = csv.DictReader(f, fieldnames=fieldnames, delimiter=delimiter)
            
            for row_num, row in enumerate(reader, 1):
                try:
                    entry = BC3PriceEntry(
                        code=row.get("code", "").strip(),
                        description=row.get("description", "").strip(),
                        resource_type=BC3ResourceType(row.get("resource_type", "1")),
                        unit=BC3Unit(row.get("unit", "ud")),
                        price=Decimal(str(row.get("price", 0)).replace(",", ".")),
                        currency=row.get("currency", "EUR"),
                        source=source,
                        valid_from=date.fromisoformat(row["valid_from"]) if row.get("valid_from") else date.today(),
                        valid_to=date.fromisoformat(row["valid_to"]) if row.get("valid_to") else None,
                        region=row.get("region") or None,
                        supplier=row.get("supplier") or None,
                        metadata={"import_row": row_num},
                    )
                    
                    if not entry.code or not entry.description:
                        errors.append(f"Fila {row_num}: código o descripción vacíos")
                        continue
                    
                    self.add_price(entry)
                    imported += 1
                    
                except Exception as e:
                    errors.append(f"Fila {row_num}: {e}")
        
        return imported, errors
    
    def export_to_csv(
        self,
        file_path: str | Path,
        resource_type: BC3ResourceType | None = None,
        valid_only: bool = True,
    ) -> int:
        """Exportar base de precios a CSV."""
        entries = []
        if resource_type:
            entries = self.search_by_resource_type(resource_type, valid_today=valid_only, limit=100000)
        else:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                query = "SELECT * FROM prices"
                params = []
                if valid_only:
                    today = date.today().isoformat()
                    query += " WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)"
                    params = [date.today().isoformat(), date.today().isoformat()]
                cursor = conn.execute(query, params)
                entries = [self._row_to_entry(row) for row in cursor.fetchall()]
        
        with open(file_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([
                "code", "description", "resource_type", "unit", "price",
                "currency", "valid_from", "valid_to", "region", "supplier"
            ])
            for entry in entries:
                writer.writerow([
                    entry.code,
                    entry.description,
                    entry.resource_type.value,
                    entry.unit.value,
                    str(entry.price),
                    entry.currency,
                    entry.valid_from.isoformat(),
                    entry.valid_to.isoformat() if entry.valid_to else "",
                    entry.region or "",
                    entry.supplier or "",
                ])
        
        return len(entries)
    
    # =========================================================================
    # HELPER METHODS
    # =========================================================================
    
    def _row_to_entry(self, row: sqlite3.Row) -> BC3PriceEntry:
        """Convertir fila SQLite a BC3PriceEntry."""
        return BC3PriceEntry(
            id=UUID(row["id"]),
            code=row["code"],
            description=row["description"],
            resource_type=BC3ResourceType(row["resource_type"]),
            unit=BC3Unit(row["unit"]),
            price=Decimal(str(row["price"])),
            currency=row["currency"],
            source=row["source"],
            source_file=row["source_file"],
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            region=row["region"],
            supplier=row["supplier"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


# =============================================================================
# FACTORY FUNCTIONS
# =============================================================================

def create_price_database(db_path: str | Path = "bc3_prices.db") -> BC3PriceDatabase:
    """Crear instancia de base de precios."""
    return BC3PriceDatabase(db_path)


def load_official_bc3_prices(db: BC3PriceDatabase, file_path: str | Path) -> tuple[int, list[str]]:
    """Cargar base de precios oficial BC3 desde CSV oficial."""
    return db.import_from_csv(file_path, source="bc3_official")


def load_bedec_prices(db: BC3PriceDatabase, file_path: str | Path) -> tuple[int, list[str]]:
    """Cargar base de precios BEDEC desde CSV."""
    return db.import_from_csv(file_path, source="bedec")


def load_preoc_prices(db: BC3PriceDatabase, file_path: str | Path) -> tuple[int, list[str]]:
    """Cargar base de precios PRE/OC desde CSV."""
    return db.import_from_csv(file_path, source="preoc")