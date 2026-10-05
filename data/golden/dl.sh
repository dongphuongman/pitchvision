#!/bin/sh
line="$1"; vid=$(echo "$line"|cut -d'|' -f1); st=$(echo "$line"|cut -d'|' -f5)
m=${st%%:*}; s=${st##*:}; a=$((m*60+s)); b=$((a+900))
[ -s raw/$vid.mp4 ] && { echo "SKIP $vid"; exit 0; }
yt-dlp --no-update --no-playlist -f "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]" \
  --download-sections "*$a-$b" --merge-output-format mp4 -o "raw/$vid.%(ext)s" \
  "https://www.youtube.com/watch?v=$vid" > logs/$vid.log 2>&1 && echo "DONE $vid" || echo "FAIL $vid"
