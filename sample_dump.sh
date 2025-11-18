# Context definitions
tj --db=test.db =work -t "Work Projects"
tj --db=test.db =personal

# Entries in insertion order
tj --db=test.db =work at=20250101/09:00 First work entry :project :urgent
tj --db=test.db =personal at=20250101/10:00 "$(cat <<'END'
This is a multi-line entry
with several lines of content
testing heredoc format
END
)"
tj --db=test.db d =work at=20250101/11:00 Complete testing
tj --db=test.db p =work at=20250102/14:00 I prefer simple solutions :philosophy
tj --db=test.db =work at=20250103/15:00 General memory entry

