#!/bin/bash
# Comprehensive test script for tj - all functionality

set -e  # Exit on error

# Use function like real-world usage: simulates alias tj='tj.py "$*"'
TESTDB="$HOME/github/tjrepo/tjai/test.db"
tj() {
    $HOME/github/tjrepo/tjai/tj.py --db="$TESTDB" "$*"
}

echo "=== Cleaning up old test database ==="
rm -f "$TESTDB"

echo ""
echo "=== BASIC ENTRY CREATION ==="
tj Simple memory entry
tj Entry with :tag embedded
tj Entry with multiple :tags :important

echo ""
echo "=== CONTEXT OPERATIONS ==="
tj =work
tj Work task in context
tj =personal Personal note
tj =0
tj Entry with no context

echo ""
echo "=== NAMED ENTRIES ==="
tj @budget Q4 budget planning
tj =work @standup Daily standup notes
tj s @budget
tj s @standup

echo ""
echo "=== PRIORITY AND STATUS ==="
tj High priority task p=1
tj Medium task p=3 s=active
tj Blocked item s=blocked
tj 1 p=2
tj 2 s=done
tj 3 @important

echo ""
echo "=== TODOS ==="
tj d Fix bug in login
tj d Write tests p=1
tj =work d @release Release checklist p=1 s=active

echo ""
echo "=== PROFILES ==="
tj p Born in Chicago
tj p Favorite language: Python

echo ""
echo "=== BOOKMARKS ==="
tj https://example.com Python docs
tj Useful article https://blog.example.com/post

echo ""
echo "=== LINKS ==="
tj Meeting agenda //https://docs.example.com/agenda
tj 'Project docs [Documentation](https://wiki.example.com/project)'
tj 'Multiple links //https://url1.com [Title](https://url2.com)'

echo ""
echo "=== CALENDAR/JOURNAL ==="
tj j tomorrow Dentist appointment
tj j mon 14:30 Weekly meeting
tj j tue 10:00 Project review
tj j fri 15:00 Sprint retrospective
tj j 16:30 Afternoon sync
tj j 20251215 10:00 Holiday party

echo ""
echo "=== TIMESTAMPS ==="
tj at=20250101/09:00 New Year planning

echo ""
echo "=== FILE INPUT ==="
echo "Multi-line entry from file
Line 2 of content
Line 3 of content" > /tmp/tj_test_file.txt
tj -f /tmp/tj_test_file.txt
rm /tmp/tj_test_file.txt

echo ""
echo "=== COMBINED METADATA ==="
tj =work @quarterly-review Q1 review p=1 s=active :planning //https://docs.example.com

echo ""
echo "=== SHOW STATUS ==="
tj

echo ""
echo "=== SHOW SPECIFIC ENTRIES ==="
tj s 1
tj s @budget
tj s @release

echo ""
echo "=== TAGS ==="
tj t 1 urgent
tj t urgent

echo ""
echo "=== QUERY ==="
tj q :important
tj q d
tj q p
tj q b
tj q p=1
tj q s=active
tj q p=1 s=active
tj q p=1 s=active :planning

echo ""
echo "=== CONTEXTS ==="
tj l c

echo ""
echo "=== CALENDAR VIEW ==="
tj c
tj c w
tj c m

echo ""
echo "=== DUMP ==="
tj dump | head -30

echo ""
echo "=== EDIT OPERATIONS ==="
# Note: Can't test interactive editor, but can test command-line edit
# tj e 1 Updated content for entry 1

echo ""
echo "=== MOVE TO CONTEXT ==="
tj m 1 work
tj m 2 personal

echo ""
echo "=== PIN TO TOP ==="
tj ^ 5

echo ""
echo "=== DELETE ==="
# tj x 99  # Would require confirmation, skip in automated test

echo ""
echo "=== FINAL STATUS ==="
tj

echo ""
echo "=== ALL TESTS COMPLETED SUCCESSFULLY ==="
