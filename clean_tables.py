import sys
from app import create_app, db
from sqlalchemy import text, inspect

app = create_app()

def truncate_tables_except_users_and_roles():
    with app.app_context():
        inspector = inspect(db.engine)
        all_tables = inspector.get_table_names()
        
        # Tables to preserve data
        keep_tables = {'users', 'roles'}
        
        # Tables to clean/truncate
        tables_to_truncate = [t for t in all_tables if t.lower() not in keep_tables]
        
        print("=" * 60)
        print(f"Total tables detected in database: {len(all_tables)}")
        print(f"Tables preserving data: {list(keep_tables)}")
        print(f"Tables to clean/truncate ({len(tables_to_truncate)}): {tables_to_truncate}")
        print("=" * 60)
        
        if not tables_to_truncate:
            print("No tables found to clean.")
            return

        with db.engine.connect() as conn:
            # Disable foreign key checks so truncating works without constraint violations
            print("Disabling foreign key checks...")
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0;"))
            
            for table_name in tables_to_truncate:
                print(f" -> Truncating table: `{table_name}`...")
                conn.execute(text(f"TRUNCATE TABLE `{table_name}`;"))
            
            print("Re-enabling foreign key checks...")
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1;"))
            conn.commit()
            
        print("=" * 60)
        print("SUCCESS! All target tables have been truncated and cleaned.")
        print("All schemas remain intact. 'users' and 'roles' data preserved.")
        print("=" * 60)

if __name__ == '__main__':
    truncate_tables_except_users_and_roles()
