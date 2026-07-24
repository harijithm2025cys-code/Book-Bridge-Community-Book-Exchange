# Book-Bridge-Community-Book-Exchange
This project is developed for hackathon(Code Insomnia )
# 📚 BookBridge — Community Book Exchange

**Problem Statement:** PS 13
**Team:** Team 27
**Members:** Harijith M, Ajay Pravin N E, Dharshan S, Sugesh S

## 📖 Overview

Many students own textbooks and reference books that remain unused after a
semester, while juniors often search for affordable study materials.
**BookBridge** is a campus platform that lets students **lend, borrow,
exchange, or donate** books within their own college community — turning
idle textbooks into accessible resources for everyone who needs them.

## 🎯 Objectives

- Reduce student spending on textbooks by encouraging reuse over repurchase
- Reduce paper waste by keeping usable books in circulation
- Build a trusted, campus-only network for lending and donating books
- Make it simple to discover the exact title/edition a student needs

## ✨ Key Features

- **Book Listing** — add books with title, author, edition, subject, condition, and cover photo
- **Smart Search & Filters** — find books by course, semester, department, or keyword
- **Lend / Borrow / Exchange / Donate** modes, including two-way exchange matching
- **In-app Requests & Chat** — coordinate directly with the book owner
- **Ratings & Trust Score** — feedback after every exchange builds reputation
- **Notifications** — real-time alerts when a wanted book becomes available
- **Campus Verification** — college email/ID sign-in keeps the community student-only

## 👥 User Roles

| Role | Description |
|---|---|
| Student (Owner) | Lists books to lend, exchange, or donate; manages incoming requests |
| Student (Seeker) | Searches the catalog, sends requests, rates completed exchanges |
| Admin / Moderator | Verifies users, monitors listings, resolves disputes and reports |

## 🏗️ System Architecture

```
Presentation Layer   → Web frontend (catalog, listing forms, request/chat UI)
Application Layer    → REST APIs (auth, listings, search, requests, notifications)
Data Layer           → Database (users, books, transactions, ratings)
```

## 🔄 How It Works

1. Sign Up & Verify (college email)
2. List a book or Search the catalog
3. Send / Accept a Request
4. Meet & Exchange the book
5. Rate the Exchange

## 🗄️ Data Model (Core Entities)

- **User** — user_id, name, college_email, dept, trust_score
- **Book** — book_id, title, author, subject, condition
- **Listing** — listing_id, book_id, owner_id, type, status
- **Request** — request_id, listing_id, seeker_id, status

## 🛠️ Tech Stack

| Layer | Technology |
|---|---|
| Frontend | HTML, CSS, JavaScript (React) |
| Backend | Node.js / Express (REST APIs) |
| Database | MongoDB / MySQL |
| Authentication | College email-based login / JWT |
| Hosting | Render / Vercel / AWS |
| Version Control | Git & GitHub |

## 🚀 Future Scope

- AI-based price & condition suggestions for listings
- Multi-campus / inter-college expansion
- Mobile app with real-time chat
- Gamified rewards for frequent donors

## 🌱 Impact

- Lower textbook costs for every student
- Less paper waste from discarded books
- A stronger, sharing-first campus culture

## 👨‍💻 Team 27

- Harijith M
- Ajay Pravin N E
- Dharshan S
- Sugesh S

---
*Submitted for Problem Statement 13 — BookBridge: Community Book Exchange*