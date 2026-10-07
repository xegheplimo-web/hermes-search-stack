import sqlite3, os, datetime

d = r"C:\Users\atton\hermes-search-stack\data"
for fn in sorted(os.listdir(d)):
    if not fn.endswith(".db"):
        continue
    p = os.path.join(d, fn)
    try:
        con = sqlite3.connect("file:" + p.replace("\\", "/") + "?mode=ro", uri=True)
        tabs = [r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
        counts = {}
        for t in tabs:
            if any(k in t.lower() for k in ("document", "event", "cache", "answer")):
                try:
                    counts[t] = con.execute('select count(*) from "%s"' % t).fetchone()[0]
                except Exception:
                    pass
        con.close()
        print("| %s | %d | %s | %d | %s |" % (
            fn,
            os.path.getsize(p),
            datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d %H:%M"),
            len(tabs),
            counts))
    except Exception as e:
        print("| %s | %d | ERR | 0 | %s |" % (fn, os.path.getsize(p), str(e)[:60]))
