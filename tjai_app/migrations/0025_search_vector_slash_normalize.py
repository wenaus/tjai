# Slash-to-space normalization in the full-text search vector. Postgres FTS
# lexes slashed compounds ("Prod/testbed") as single file-path tokens that a
# word query for "testbed" can never match. The trigger function now translates
# '/' to ' ' before to_tsvector; query construction applies the identical
# normalization (services.fts_normalize) so both sides tokenize the same way.
# The trigger itself (entries_search_vector_trigger, migration 0013) is
# unchanged — it executes this function.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tjai_app', '0024_register_backup_capcom_state'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                CREATE OR REPLACE FUNCTION entries_search_vector_update() RETURNS trigger AS $$
                BEGIN
                    NEW.search_vector := to_tsvector('english', translate(COALESCE(NEW.content, ''), '/', ' '));
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """,
            reverse_sql="""
                CREATE OR REPLACE FUNCTION entries_search_vector_update() RETURNS trigger AS $$
                BEGIN
                    NEW.search_vector := to_tsvector('english', COALESCE(NEW.content, ''));
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
            """,
        ),
        # Rebuild every stored vector under the new normalization.
        migrations.RunSQL(
            sql="UPDATE entries SET search_vector = to_tsvector('english', translate(COALESCE(content, ''), '/', ' '));",
            reverse_sql="UPDATE entries SET search_vector = to_tsvector('english', COALESCE(content, ''));",
        ),
    ]
