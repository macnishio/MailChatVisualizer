document.addEventListener('DOMContentLoaded', function() {
    const messagesContainer = document.querySelector('.messages-container');
    const pageSizeSelect = document.getElementById('pageSizeSelect');
    const sortSelect = document.getElementById('sortSelect');
    
    // ページサイズと並び替えの変更を処理する関数
    function updateUrlAndReload(params) {
        const currentUrl = new URL(window.location.href);
        const contact = currentUrl.searchParams.get('contact');
        const search = currentUrl.searchParams.get('search');
        
        const newParams = new URLSearchParams();
        if (contact) newParams.set('contact', contact);
        if (search) newParams.set('search', search);
        
        // 渡されたパラメータを追加
        Object.entries(params).forEach(([key, value]) => {
            newParams.set(key, value);
        });
        
        // アニメーション効果を追加してページ遷移
        document.body.style.opacity = '0.5';
        document.body.style.transition = 'opacity 0.3s ease';
        
        setTimeout(() => {
            window.location.href = `${window.location.pathname}?${newParams.toString()}`;
        }, 300);
    }
    
    // ページサイズの変更を処理
    if (pageSizeSelect) {
        pageSizeSelect.addEventListener('change', function() {
            updateUrlAndReload({
                'per_page': this.value,
                'page': 1 // ページサイズ変更時はページを1に戻す
            });
        });
    }
    
    // 並び替えの変更を処理
    if (sortSelect) {
        sortSelect.addEventListener('change', function() {
            updateUrlAndReload({
                'sort_by': this.value,
                'page': 1 // 並び替え変更時はページを1に戻す
            });
        });
    }
    
    // メッセージの展開機能
    document.querySelectorAll('.show-full-message').forEach(button => {
        button.addEventListener('click', function() {
            const preview = this.closest('.message-preview');
            const full = preview.nextElementSibling;
            preview.style.display = 'none';
            full.style.display = 'block';
        });
    });
    const currentContact = new URLSearchParams(window.location.search).get('contact');
    const searchInput = document.getElementById('contactSearch');
    const searchResults = document.getElementById('searchResults');
    let debounceTimer;
    let currentPage = 1;
    let loading = false;
    let hasNextPage = true;

    if (messagesContainer) {
        messagesContainer.scrollTop = messagesContainer.scrollHeight;

        // Infinite scroll
        messagesContainer.addEventListener('scroll', function() {
            if (loading || !hasNextPage) return;

            const threshold = 100;
            if (messagesContainer.scrollHeight - messagesContainer.scrollTop - messagesContainer.clientHeight < threshold) {
                loading = true;
                currentPage++;

                const searchParams = new URLSearchParams(window.location.search);
                searchParams.set('page', currentPage);

                fetch(`/?${searchParams.toString()}`, {
                    headers: {
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                })
                .then(response => response.json())
                .then(data => {
                    if (data.messages && data.messages.length > 0) {
                        data.messages.forEach(message => {
                                const messageDiv = document.createElement('div');
                                messageDiv.className = `message ${message.is_sent ? 'sent' : 'received'}`;
                                messageDiv.innerHTML = `
                                    <div class="message-content">
                                        ${message.body || ''}
                                        <div class="message-time">
                                            ${new Date(message.date).toLocaleString()}
                                        </div>
                                    </div>
                                `;
                                messagesContainer.appendChild(messageDiv);
                            });
                        }
                        hasNextPage = data.has_next;
                    } else {
                        hasNextPage = false;
                    }
                })
                .catch(error => console.error('Error:', error))
                .finally(() => {
                    loading = false;
                });
            }
        });
    }

    if (searchInput && searchResults) {
        let debounceTimer;
        
        searchInput.addEventListener('input', function() {
            clearTimeout(debounceTimer);
            const query = this.value.trim();
            console.log('入力値:', query);  // デバッグログ
            
            if (query.length < 2) {
                searchResults.classList.remove('show');
                return;
            }
            
            debounceTimer = setTimeout(() => {
                console.log('API呼び出し:', query);  // デバッグログ
                const apiUrl = `/api/search_contacts?q=${encodeURIComponent(query)}`;
                fetch(apiUrl, {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }
                })
                    .then(response => {
                        if (!response.ok) {
                            throw new Error(`Network response was not ok: ${response.status}`);
                        }
                        return response.json();
                    })
                    .then(data => {
                        console.log('検索結果:', data);  // デバッグログ
                        searchResults.innerHTML = '';
                        
                        if (Array.isArray(data) && data.length > 0) {
                            data.forEach(contact => {
                                const item = document.createElement('a');
                                item.className = 'dropdown-item';
                                item.href = `/?contact=${encodeURIComponent(contact)}`;
                                item.textContent = contact;
                                item.setAttribute('role', 'option');
                                searchResults.appendChild(item);
                            });
                            searchResults.classList.add('show');
                        } else {
                            searchResults.classList.remove('show');
                        }
                    })
                    .catch(error => {
                        console.error('検索エラー:', error);
                        searchResults.classList.remove('show');
                    });
            }, 300);
        });

        // クリック外での非表示
        document.addEventListener('click', function(e) {
            if (!searchInput.contains(e.target) && !searchResults.contains(e.target)) {
                searchResults.classList.remove('show');
            }
        });
    }
    // Add active class to current contact
    if (currentContact) {
        const contactLinks = document.querySelectorAll('.contact-item');
        contactLinks.forEach(link => {
            if (link.href.includes(encodeURIComponent(currentContact))) {
                link.classList.add('active');
            }
        });
    }
});
