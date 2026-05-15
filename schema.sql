-- ============================================================
-- AI Code Reviewer — Supabase Schema + Seed Data
-- ============================================================


-- ── 1. CREATE TABLE ─────────────────────────────────────────

create table if not exists test_cases (
    id          text primary key,           -- e.g. "SEC-001"
    dimension   text not null,              -- "Security" | "Correctness" | ...
    weight      numeric(4,2) not null,      -- 0.20 | 0.15 | ...
    case_type   text not null,              -- "true_positive" | "false_positive"
    language    text not null default 'go',
    code        text not null,
    gt_issue    text,                       -- ground truth issue (null = clean code)
    gt_line     integer,
    gt_severity text,                       -- "critical" | "high" | "medium" | "low" | null
    gt_standard text,
    gt_fix      text,
    created_at  timestamptz default now()
);


-- ── 2. SEED DATA ─────────────────────────────────────────────

-- SECURITY (20%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'SEC-001', 'Security', 0.20, 'true_positive', 'go',
    $code$
func getUser(db *sql.DB, username string) (*User, error) {
    query := "SELECT * FROM users WHERE username = '" + username + "'"
    row := db.QueryRow(query)
    // ...
}$code$,
    'SQL Injection via string concatenation',
    2, 'critical', 'OWASP A03:2021',
    'parameterized query'
),
(
    'SEC-002', 'Security', 0.20, 'true_positive', 'go',
    $code$
func connectDB() *sql.DB {
    dsn := "admin:SuperSecret123@tcp(localhost:3306)/mydb"
    db, _ := sql.Open("mysql", dsn)
    return db
}$code$,
    'Hardcoded credentials',
    2, 'critical', 'OWASP Secure Coding Practices',
    'environment variable or secrets manager'
),
(
    'SEC-003', 'Security', 0.20, 'true_positive', 'go',
    $code$
func getOrder(w http.ResponseWriter, r *http.Request) {
    orderID := r.URL.Query().Get("order_id")
    order, _ := db.GetOrderByID(orderID)
    json.NewEncoder(w).Encode(order)
}$code$,
    'IDOR - missing authorization check',
    3, 'high', 'OWASP A01:2021',
    'verify order.UserID == currentUserID'
),
(
    'SEC-004', 'Security', 0.20, 'false_positive', 'go',
    $code$
func getUser(db *sql.DB, userID int) (*User, error) {
    var user User
    err := db.QueryRow(
        "SELECT id, name, email FROM users WHERE id = ?",
        userID,
    ).Scan(&user.ID, &user.Name, &user.Email)
    if err != nil {
        return nil, err
    }
    return &user, nil
}$code$,
    null, null, null, null, null
);

-- CORRECTNESS (20%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'COR-001', 'Correctness', 0.20, 'true_positive', 'go',
    $code$
func getLastN(items []string, n int) []string {
    return items[len(items)-n:]
}

result := getLastN([]string{"a", "b"}, 5)$code$,
    'Panic when n > len(items) - missing edge case',
    2, 'high', 'Correctness - edge case handling',
    'check if n > len(items) before slice'
),
(
    'COR-002', 'Correctness', 0.20, 'true_positive', 'go',
    $code$
func getUserEmail(userID int) string {
    user, _ := db.FindUser(userID)
    return user.Email
}$code$,
    'Nil pointer dereference - error ignored',
    2, 'critical', 'Correctness - error handling',
    'check err and user != nil before accessing .Email'
),
(
    'COR-003', 'Correctness', 0.20, 'true_positive', 'go',
    $code$
var counter int

func increment() {
    counter++
}

func main() {
    for i := 0; i < 1000; i++ {
        go increment()
    }
}$code$,
    'Race condition - concurrent writes to counter',
    4, 'high', 'Correctness - concurrency safety',
    'use sync/atomic or sync.Mutex'
),
(
    'COR-004', 'Correctness', 0.20, 'false_positive', 'go',
    $code$
func divide(a, b float64) (float64, error) {
    if b == 0 {
        return 0, errors.New("division by zero")
    }
    return a / b, nil
}$code$,
    null, null, null, null, null
);

-- READABILITY (15%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'READ-001', 'Readability', 0.15, 'true_positive', 'go',
    $code$
func processOrder(order Order) error {
    if order.ID == 0 { return errors.New("invalid id") }
    if order.UserID == 0 { return errors.New("invalid user") }
    if len(order.Items) == 0 { return errors.New("no items") }
    total := 0.0
    for _, item := range order.Items {
        total += item.Price * float64(item.Quantity)
    }
    if order.Discount > 0 {
        total = total * (1 - order.Discount)
    }
    _, err := db.Exec("INSERT INTO orders ...", order.ID, total)
    if err != nil { return err }
    msg := fmt.Sprintf("Your order %d total is %.2f", order.ID, total)
    smtp.SendMail("...", nil, "no-reply@shop.com", []string{order.Email}, []byte(msg))
    return nil
}$code$,
    'God function - SRP violation, does 4 things',
    null, 'medium', 'Google Engineering Practices',
    'split into validateOrder, calculateTotal, saveOrder, notifyUser'
),
(
    'READ-002', 'Readability', 0.15, 'true_positive', 'go',
    $code$
func calc(x []float64, t int) float64 {
    r := 0.0
    for _, v := range x {
        r += v
    }
    if t == 1 {
        return r / float64(len(x))
    }
    return r
}$code$,
    'Poor naming - x, t, r, v, calc are meaningless',
    null, 'medium', 'Google Engineering Practices - meaningful names',
    'rename to calculateSum/Average(prices []float64, mode int)'
),
(
    'READ-003', 'Readability', 0.15, 'false_positive', 'go',
    $code$
func calculateDiscount(price float64, membershipTier string) float64 {
    switch membershipTier {
    case "gold":
        return price * 0.20
    case "silver":
        return price * 0.10
    default:
        return 0
    }
}$code$,
    null, null, null, null, null
);

-- DESIGN (15%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'DES-001', 'Design', 0.15, 'true_positive', 'go',
    $code$
type OrderService struct{}

func (s *OrderService) PlaceOrder(order Order) error {
    db, _ := sql.Open("mysql", "user:pass@/dbname")
    _, err := db.Exec("INSERT INTO orders ...", order.ID)
    sg := sendgrid.NewSendClient("SG.api_key")
    sg.Send(buildEmail(order))
    return err
}$code$,
    'Tight coupling to MySQL and SendGrid directly',
    null, 'high', 'Google Engineering Practices - dependency injection',
    'inject OrderRepository and Notifier interfaces'
),
(
    'DES-002', 'Design', 0.15, 'true_positive', 'go',
    $code$
type NumberFactory interface {
    CreateNumber(val int) Number
}
type ConcreteNumberFactory struct{}
func (f *ConcreteNumberFactory) CreateNumber(val int) Number {
    return &ConcreteNumber{value: val}
}
func Add(factory NumberFactory, a, b int) int {
    numA := factory.CreateNumber(a)
    numB := factory.CreateNumber(b)
    return numA.Value() + numB.Value()
}$code$,
    'Over-engineering - Factory pattern just to add two numbers',
    null, 'medium', 'Google Engineering Practices - avoid unnecessary complexity',
    'func Add(a, b int) int { return a + b }'
),
(
    'DES-003', 'Design', 0.15, 'false_positive', 'go',
    $code$
type UserRepository interface {
    FindByID(id int) (*User, error)
    Save(user *User) error
}
type UserService struct {
    repo UserRepository
}
func NewUserService(repo UserRepository) *UserService {
    return &UserService{repo: repo}
}$code$,
    null, null, null, null, null
);

-- TESTABILITY (15%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'TEST-001', 'Testability', 0.15, 'true_positive', 'go',
    $code$
func GetCurrentUserReport() string {
    user, _ := db.QueryRow("SELECT * FROM users WHERE id = ?", getCurrentUserID())
    now := time.Now()
    return fmt.Sprintf("Report for %s at %s", user.Name, now.Format(time.RFC3339))
}$code$,
    'Untestable - depends on global db, time.Now, getCurrentUserID',
    null, 'high', 'DORA Metrics - testability',
    'inject db, clock, userID as parameters'
),
(
    'TEST-002', 'Testability', 0.15, 'true_positive', 'go',
    $code$
func TestDivide(t *testing.T) {
    result, err := divide(10, 2)
    if err != nil {
        t.Fatal(err)
    }
    if result != 5 {
        t.Errorf("expected 5, got %f", result)
    }
}$code$,
    'Missing edge case tests - no test for divide by zero',
    null, 'medium', 'Google Engineering Practices - test edge cases',
    'add TestDivide_ByZero that expects error return'
),
(
    'TEST-003', 'Testability', 0.15, 'false_positive', 'go',
    $code$
func TestGetLastN(t *testing.T) {
    tests := []struct {
        items    []string
        n        int
        expected []string
    }{
        {[]string{"a", "b", "c"}, 2, []string{"b", "c"}},
        {[]string{"a"}, 1, []string{"a"}},
        {[]string{"a", "b"}, 5, []string{"a", "b"}},
        {[]string{}, 1, []string{}},
    }
    for _, tt := range tests {
        result := getLastN(tt.items, tt.n)
        if !reflect.DeepEqual(result, tt.expected) {
            t.Errorf("got %v, want %v", result, tt.expected)
        }
    }
}$code$,
    null, null, null, null, null
);

-- PERFORMANCE (10%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'PERF-001', 'Performance', 0.10, 'true_positive', 'go',
    $code$
func getOrdersWithItems(db *sql.DB) ([]Order, error) {
    orders, _ := db.Query("SELECT * FROM orders")
    for _, order := range orders {
        order.Items, _ = db.Query(
            "SELECT * FROM items WHERE order_id = ?", order.ID,
        )
    }
    return orders, nil
}$code$,
    'N+1 query - 100 orders = 101 DB queries',
    4, 'high', 'Database best practices - batch loading',
    'use JOIN or batch load with WHERE order_id IN (...)'
),
(
    'PERF-002', 'Performance', 0.10, 'true_positive', 'go',
    $code$
func findDuplicates(items []string) []string {
    var duplicates []string
    for i := 0; i < len(items); i++ {
        for j := i + 1; j < len(items); j++ {
            if items[i] == items[j] {
                duplicates = append(duplicates, items[i])
            }
        }
    }
    return duplicates
}$code$,
    'O(n²) nested loop',
    null, 'medium', 'Algorithm complexity',
    'use map[string]int for O(n) solution'
),
(
    'PERF-003', 'Performance', 0.10, 'false_positive', 'go',
    $code$
func findDuplicates(items []string) []string {
    seen := make(map[string]bool)
    var duplicates []string
    for _, item := range items {
        if seen[item] {
            duplicates = append(duplicates, item)
        }
        seen[item] = true
    }
    return duplicates
}$code$,
    null, null, null, null, null
);

-- IDIOMATIC (5%)

insert into test_cases (id, dimension, weight, case_type, language, code, gt_issue, gt_line, gt_severity, gt_standard, gt_fix) values
(
    'IDIO-001', 'Idiomatic', 0.05, 'true_positive', 'go',
    $code$
func readFile(path string) string {
    content, err := os.ReadFile(path)
    if err != nil {
        panic(err)
    }
    return string(content)
}$code$,
    'Using panic for recoverable error',
    null, 'low', 'Effective Go - error handling',
    'return string(content), nil and return empty string, err'
),
(
    'IDIO-002', 'Idiomatic', 0.05, 'true_positive', 'go',
    $code$
type userManager struct{}

func (um *userManager) GetUserId(user User) int {
    return user.Id
}

func (um *userManager) getUserURL(user User) string {
    return user.Url
}$code$,
    'Acronym casing not following Go convention - Id should be ID, Url should be URL',
    null, 'low', 'Effective Go - initialisms',
    'rename Id->ID, Url->URL everywhere'
),
(
    'IDIO-003', 'Idiomatic', 0.05, 'false_positive', 'go',
    $code$
type UserService struct {
    repo UserRepository
}

func (s *UserService) FindByID(id int) (*User, error) {
    if id <= 0 {
        return nil, fmt.Errorf("invalid user id: %d", id)
    }
    return s.repo.FindByID(id)
}$code$,
    null, null, null, null, null
);


-- ── 3. VERIFY ────────────────────────────────────────────────

select
    dimension,
    count(*) as total,
    count(*) filter (where case_type = 'true_positive')  as tp,
    count(*) filter (where case_type = 'false_positive') as fp
from test_cases
group by dimension
order by dimension;