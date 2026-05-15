import sqlite3

DB_PATH = r"C:\ReviewMe\data.db"

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS test_cases (
    id          TEXT PRIMARY KEY,
    dimension   TEXT NOT NULL,
    weight      REAL NOT NULL,
    case_type   TEXT NOT NULL,
    language    TEXT NOT NULL DEFAULT 'go',
    code        TEXT NOT NULL,
    gt_issue    TEXT,
    gt_line     INTEGER,
    gt_severity TEXT,
    gt_standard TEXT,
    gt_fix      TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

ROWS = [
    # SECURITY
    ("SEC-001", "Security", 0.20, "true_positive", "go",
     'func getUser(db *sql.DB, username string) (*User, error) {\n    query := "SELECT * FROM users WHERE username = \'" + username + "\'"\n    row := db.QueryRow(query)\n    // ...\n}',
     "SQL Injection via string concatenation", 2, "critical", "OWASP A03:2021", "parameterized query"),
    ("SEC-002", "Security", 0.20, "true_positive", "go",
     'func connectDB() *sql.DB {\n    dsn := "admin:SuperSecret123@tcp(localhost:3306)/mydb"\n    db, _ := sql.Open("mysql", dsn)\n    return db\n}',
     "Hardcoded credentials", 2, "critical", "OWASP Secure Coding Practices", "environment variable or secrets manager"),
    ("SEC-003", "Security", 0.20, "true_positive", "go",
     'func getOrder(w http.ResponseWriter, r *http.Request) {\n    orderID := r.URL.Query().Get("order_id")\n    order, _ := db.GetOrderByID(orderID)\n    json.NewEncoder(w).Encode(order)\n}',
     "IDOR - missing authorization check", 3, "high", "OWASP A01:2021", "verify order.UserID == currentUserID"),
    ("SEC-004", "Security", 0.20, "false_positive", "go",
     'func getUser(db *sql.DB, userID int) (*User, error) {\n    var user User\n    err := db.QueryRow(\n        "SELECT id, name, email FROM users WHERE id = ?",\n        userID,\n    ).Scan(&user.ID, &user.Name, &user.Email)\n    if err != nil {\n        return nil, err\n    }\n    return &user, nil\n}',
     None, None, None, None, None),

    # CORRECTNESS
    ("COR-001", "Correctness", 0.20, "true_positive", "go",
     'func getLastN(items []string, n int) []string {\n    return items[len(items)-n:]\n}\n\nresult := getLastN([]string{"a", "b"}, 5)',
     "Panic when n > len(items) - missing edge case", 2, "high", "Correctness - edge case handling", "check if n > len(items) before slice"),
    ("COR-002", "Correctness", 0.20, "true_positive", "go",
     'func getUserEmail(userID int) string {\n    user, _ := db.FindUser(userID)\n    return user.Email\n}',
     "Nil pointer dereference - error ignored", 2, "critical", "Correctness - error handling", "check err and user != nil before accessing .Email"),
    ("COR-003", "Correctness", 0.20, "true_positive", "go",
     'var counter int\n\nfunc increment() {\n    counter++\n}\n\nfunc main() {\n    for i := 0; i < 1000; i++ {\n        go increment()\n    }\n}',
     "Race condition - concurrent writes to counter", 4, "high", "Correctness - concurrency safety", "use sync/atomic or sync.Mutex"),
    ("COR-004", "Correctness", 0.20, "false_positive", "go",
     'func divide(a, b float64) (float64, error) {\n    if b == 0 {\n        return 0, errors.New("division by zero")\n    }\n    return a / b, nil\n}',
     None, None, None, None, None),

    # READABILITY
    ("READ-001", "Readability", 0.15, "true_positive", "go",
     'func processOrder(order Order) error {\n    if order.ID == 0 { return errors.New("invalid id") }\n    if order.UserID == 0 { return errors.New("invalid user") }\n    if len(order.Items) == 0 { return errors.New("no items") }\n    total := 0.0\n    for _, item := range order.Items {\n        total += item.Price * float64(item.Quantity)\n    }\n    if order.Discount > 0 {\n        total = total * (1 - order.Discount)\n    }\n    _, err := db.Exec("INSERT INTO orders ...", order.ID, total)\n    if err != nil { return err }\n    msg := fmt.Sprintf("Your order %d total is %.2f", order.ID, total)\n    smtp.SendMail("...", nil, "no-reply@shop.com", []string{order.Email}, []byte(msg))\n    return nil\n}',
     "God function - SRP violation, does 4 things", None, "medium", "Google Engineering Practices", "split into validateOrder, calculateTotal, saveOrder, notifyUser"),
    ("READ-002", "Readability", 0.15, "true_positive", "go",
     'func calc(x []float64, t int) float64 {\n    r := 0.0\n    for _, v := range x {\n        r += v\n    }\n    if t == 1 {\n        return r / float64(len(x))\n    }\n    return r\n}',
     "Poor naming - x, t, r, v, calc are meaningless", None, "medium", "Google Engineering Practices - meaningful names", "rename to calculateSum/Average(prices []float64, mode int)"),
    ("READ-003", "Readability", 0.15, "false_positive", "go",
     'func calculateDiscount(price float64, membershipTier string) float64 {\n    switch membershipTier {\n    case "gold":\n        return price * 0.20\n    case "silver":\n        return price * 0.10\n    default:\n        return 0\n    }\n}',
     None, None, None, None, None),

    # DESIGN
    ("DES-001", "Design", 0.15, "true_positive", "go",
     'type OrderService struct{}\n\nfunc (s *OrderService) PlaceOrder(order Order) error {\n    db, _ := sql.Open("mysql", "user:pass@/dbname")\n    _, err := db.Exec("INSERT INTO orders ...", order.ID)\n    sg := sendgrid.NewSendClient("SG.api_key")\n    sg.Send(buildEmail(order))\n    return err\n}',
     "Tight coupling to MySQL and SendGrid directly", None, "high", "Google Engineering Practices - dependency injection", "inject OrderRepository and Notifier interfaces"),
    ("DES-002", "Design", 0.15, "true_positive", "go",
     'type NumberFactory interface {\n    CreateNumber(val int) Number\n}\ntype ConcreteNumberFactory struct{}\nfunc (f *ConcreteNumberFactory) CreateNumber(val int) Number {\n    return &ConcreteNumber{value: val}\n}\nfunc Add(factory NumberFactory, a, b int) int {\n    numA := factory.CreateNumber(a)\n    numB := factory.CreateNumber(b)\n    return numA.Value() + numB.Value()\n}',
     "Over-engineering - Factory pattern just to add two numbers", None, "medium", "Google Engineering Practices - avoid unnecessary complexity", "func Add(a, b int) int { return a + b }"),
    ("DES-003", "Design", 0.15, "false_positive", "go",
     'type UserRepository interface {\n    FindByID(id int) (*User, error)\n    Save(user *User) error\n}\ntype UserService struct {\n    repo UserRepository\n}\nfunc NewUserService(repo UserRepository) *UserService {\n    return &UserService{repo: repo}\n}',
     None, None, None, None, None),

    # TESTABILITY
    ("TEST-001", "Testability", 0.15, "true_positive", "go",
     'func GetCurrentUserReport() string {\n    user, _ := db.QueryRow("SELECT * FROM users WHERE id = ?", getCurrentUserID())\n    now := time.Now()\n    return fmt.Sprintf("Report for %s at %s", user.Name, now.Format(time.RFC3339))\n}',
     "Untestable - depends on global db, time.Now, getCurrentUserID", None, "high", "DORA Metrics - testability", "inject db, clock, userID as parameters"),
    ("TEST-002", "Testability", 0.15, "true_positive", "go",
     'func TestDivide(t *testing.T) {\n    result, err := divide(10, 2)\n    if err != nil {\n        t.Fatal(err)\n    }\n    if result != 5 {\n        t.Errorf("expected 5, got %f", result)\n    }\n}',
     "Missing edge case tests - no test for divide by zero", None, "medium", "Google Engineering Practices - test edge cases", "add TestDivide_ByZero that expects error return"),
    ("TEST-003", "Testability", 0.15, "false_positive", "go",
     'func TestGetLastN(t *testing.T) {\n    tests := []struct {\n        items    []string\n        n        int\n        expected []string\n    }{\n        {[]string{"a", "b", "c"}, 2, []string{"b", "c"}},\n        {[]string{"a"}, 1, []string{"a"}},\n        {[]string{"a", "b"}, 5, []string{"a", "b"}},\n        {[]string{}, 1, []string{}},\n    }\n    for _, tt := range tests {\n        result := getLastN(tt.items, tt.n)\n        if !reflect.DeepEqual(result, tt.expected) {\n            t.Errorf("got %v, want %v", result, tt.expected)\n        }\n    }\n}',
     None, None, None, None, None),

    # PERFORMANCE
    ("PERF-001", "Performance", 0.10, "true_positive", "go",
     'func getOrdersWithItems(db *sql.DB) ([]Order, error) {\n    orders, _ := db.Query("SELECT * FROM orders")\n    for _, order := range orders {\n        order.Items, _ = db.Query(\n            "SELECT * FROM items WHERE order_id = ?", order.ID,\n        )\n    }\n    return orders, nil\n}',
     "N+1 query - 100 orders = 101 DB queries", 4, "high", "Database best practices - batch loading", "use JOIN or batch load with WHERE order_id IN (...)"),
    ("PERF-002", "Performance", 0.10, "true_positive", "go",
     'func findDuplicates(items []string) []string {\n    var duplicates []string\n    for i := 0; i < len(items); i++ {\n        for j := i + 1; j < len(items); j++ {\n            if items[i] == items[j] {\n                duplicates = append(duplicates, items[i])\n            }\n        }\n    }\n    return duplicates\n}',
     "O(n²) nested loop", None, "medium", "Algorithm complexity", "use map[string]int for O(n) solution"),
    ("PERF-003", "Performance", 0.10, "false_positive", "go",
     'func findDuplicates(items []string) []string {\n    seen := make(map[string]bool)\n    var duplicates []string\n    for _, item := range items {\n        if seen[item] {\n            duplicates = append(duplicates, item)\n        }\n        seen[item] = true\n    }\n    return duplicates\n}',
     None, None, None, None, None),

    # IDIOMATIC
    ("IDIO-001", "Idiomatic", 0.05, "true_positive", "go",
     'func readFile(path string) string {\n    content, err := os.ReadFile(path)\n    if err != nil {\n        panic(err)\n    }\n    return string(content)\n}',
     "Using panic for recoverable error", None, "low", "Effective Go - error handling", "return string(content), nil and return empty string, err"),
    ("IDIO-002", "Idiomatic", 0.05, "true_positive", "go",
     'type userManager struct{}\n\nfunc (um *userManager) GetUserId(user User) int {\n    return user.Id\n}\n\nfunc (um *userManager) getUserURL(user User) string {\n    return user.Url\n}',
     "Acronym casing not following Go convention - Id should be ID, Url should be URL", None, "low", "Effective Go - initialisms", "rename Id->ID, Url->URL everywhere"),
    ("IDIO-003", "Idiomatic", 0.05, "false_positive", "go",
     'type UserService struct {\n    repo UserRepository\n}\n\nfunc (s *UserService) FindByID(id int) (*User, error) {\n    if id <= 0 {\n        return nil, fmt.Errorf("invalid user id: %d", id)\n    }\n    return s.repo.FindByID(id)\n}',
     None, None, None, None, None),

    # SECURITY — additional TPs
    ("SEC-005", "Security", 0.20, "true_positive", "go",
     'func generateToken() string {\n    b := make([]byte, 16)\n    for i := range b {\n        b[i] = byte(rand.Intn(256))\n    }\n    return hex.EncodeToString(b)\n}',
     "Insecure random - math/rand is not cryptographically secure for tokens", 3, "critical", "OWASP A02:2021", "use crypto/rand.Read instead of math/rand"),
    ("SEC-006", "Security", 0.20, "true_positive", "go",
     'func renderProfile(w http.ResponseWriter, username string) {\n    fmt.Fprintf(w, "<h1>Hello, %s</h1>", username)\n}',
     "XSS via unescaped user input in HTML output", 2, "high", "OWASP A03:2021", "use html.EscapeString(username) or html/template"),

    # CORRECTNESS — additional TPs
    ("COR-005", "Correctness", 0.20, "true_positive", "go",
     'func sumAll(nums []int) int {\n    total := 0\n    for _, n := range nums {\n        total += n\n    }\n    return total\n}\n\nresult := sumAll(nil)',
     "No nil/empty guard — caller can pass nil slice silently, return 0 masks bug", 1, "medium", "Correctness - edge case handling", "document nil behavior or add explicit empty check"),
    ("COR-006", "Correctness", 0.20, "true_positive", "go",
     'func closeAll(conns []*sql.DB) {\n    for _, db := range conns {\n        db.Close()\n    }\n}',
     "Errors from Close() silently discarded - connection leaks go undetected", 3, "high", "Correctness - error handling", "collect and return close errors"),

    # READABILITY — additional TPs
    ("READ-004", "Readability", 0.15, "true_positive", "go",
     'func applyDiscount(price float64) float64 {\n    if price > 1000 {\n        return price * 0.85\n    }\n    return price * 0.95\n}',
     "Magic numbers 0.85, 0.95, 1000 — intent unclear without named constants", None, "medium", "Google Engineering Practices", "extract const PremiumDiscount, StandardDiscount, PremiumThreshold"),
    ("READ-005", "Readability", 0.15, "true_positive", "go",
     'func handle(r *Request) error {\n    if r != nil {\n        if r.User != nil {\n            if r.User.Active {\n                if r.Payload != nil {\n                    return process(r.Payload)\n                }\n            }\n        }\n    }\n    return nil\n}',
     "Deep nesting 4 levels — hard to follow control flow", None, "medium", "Google Engineering Practices", "use early returns to flatten nesting"),

    # DESIGN — additional TPs
    ("DES-004", "Design", 0.15, "true_positive", "go",
     'var db *sql.DB\n\nfunc init() {\n    var err error\n    db, err = sql.Open("postgres", os.Getenv("DATABASE_URL"))\n    if err != nil {\n        log.Fatal(err)\n    }\n}',
     "Global mutable state via package-level db — untestable and hidden coupling", None, "high", "Google Engineering Practices - dependency injection", "pass *sql.DB as constructor argument"),
    ("DES-005", "Design", 0.15, "true_positive", "go",
     'type UserService interface {\n    Create(u User) error\n    Update(u User) error\n    Delete(id int) error\n    FindByID(id int) (*User, error)\n    FindByEmail(email string) (*User, error)\n    List() ([]User, error)\n    Deactivate(id int) error\n    ResetPassword(id int, pw string) error\n}',
     "Fat interface - 8 methods forces implementors to stub all, violates ISP", None, "medium", "Google Engineering Practices - interface segregation", "split into smaller focused interfaces: UserReader, UserWriter"),

    # TESTABILITY — additional TPs
    ("TEST-004", "Testability", 0.15, "true_positive", "go",
     'func loadConfig() Config {\n    f, _ := os.Open("/etc/app/config.json")\n    defer f.Close()\n    var cfg Config\n    json.NewDecoder(f).Decode(&cfg)\n    return cfg\n}',
     "Hard-coded file path makes function untestable without filesystem setup", None, "high", "DORA Metrics - testability", "accept io.Reader parameter instead of opening file internally"),
    ("TEST-005", "Testability", 0.15, "true_positive", "go",
     'func TestProcessOrder(t *testing.T) {\n    order := Order{ID: 1, Items: []Item{{Price: 10.0, Quantity: 2}}}\n    err := ProcessOrder(order)\n    if err != nil {\n        t.Fatal(err)\n    }\n}',
     "Test only covers happy path - no test for empty items, zero price, or invalid ID", None, "medium", "Google Engineering Practices - test edge cases", "add table-driven subtests for empty items, zero price, invalid ID"),

    # PERFORMANCE — additional TPs
    ("PERF-004", "Performance", 0.10, "true_positive", "go",
     'func buildQuery(filters []string) string {\n    query := "SELECT * FROM users WHERE "\n    for _, f := range filters {\n        query += f + " AND "\n    }\n    return query\n}',
     "String concatenation in loop - O(n²) allocations, use strings.Builder", 3, "high", "Algorithm complexity", "use strings.Builder or strings.Join"),
    ("PERF-005", "Performance", 0.10, "true_positive", "go",
     'func countWords(words []string) map[string]int {\n    counts := map[string]int{}\n    for _, w := range words {\n        counts[w] = counts[w] + 1\n    }\n    return counts\n}\n\nfunc topWord(words []string) string {\n    counts := countWords(words)\n    best := ""\n    for _, w := range words {\n        if counts[w] > counts[best] {\n            best = w\n        }\n    }\n    return best\n}',
     "countWords called once but topWord re-iterates words doing redundant map lookups", None, "medium", "Algorithm complexity", "track max during single pass in countWords"),

    # IDIOMATIC — additional TPs
    ("IDIO-004", "Idiomatic", 0.05, "true_positive", "go",
     'func NewUser(name string) *User {\n    u := new(User)\n    u.Name = name\n    u.Active = true\n    return u\n}',
     "new(User) then field assignment is non-idiomatic - use struct literal", 2, "low", "Effective Go - composite literals", "return &User{Name: name, Active: true}"),
    ("IDIO-005", "Idiomatic", 0.05, "true_positive", "go",
     'func divide(a, b float64) (result float64, err error) {\n    if b == 0 {\n        err = errors.New("division by zero")\n        return\n    }\n    result = a / b\n    return\n}',
     "Naked returns with named results reduce readability in non-trivial functions", None, "low", "Effective Go - named return values", "use explicit return values: return a/b, nil"),
]

INSERT_SQL = """
INSERT OR IGNORE INTO test_cases
    (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(CREATE_TABLE)
    cur.executemany(INSERT_SQL, ROWS)
    con.commit()

    print("=== Verify ===")
    for row in cur.execute("""
        SELECT dimension, count(*) total,
               sum(case_type='true_positive') tp,
               sum(case_type='false_positive') fp
        FROM test_cases
        GROUP BY dimension
        ORDER BY dimension
    """):
        print(f"{row[0]:<12} total={row[1]} tp={row[2]} fp={row[3]}")

    con.close()
    print(f"\nDB created: {DB_PATH}")

if __name__ == "__main__":
    main()
