(() => {
  // Rapor ekranındaki "Yazdır / PDF" düğmesi (inline handler yerine).
  document.querySelectorAll("[data-print-button]").forEach((button) => {
    button.addEventListener("click", () => window.print());
  });

  const toggle = document.querySelector(".menu-toggle");
  const sidebar = document.querySelector("#sidebar");
  if (toggle && sidebar) {
    const setMenuState = (isOpen) => {
      toggle.setAttribute("aria-expanded", String(isOpen));
      toggle.setAttribute("aria-label", isOpen ? "Menüyü kapat" : "Menüyü aç");
    };
    const closeSidebar = () => {
      sidebar.classList.remove("open");
      setMenuState(false);
    };

    toggle.addEventListener("click", () => {
      setMenuState(sidebar.classList.toggle("open"));
    });

    document.addEventListener("click", (event) => {
      if (window.innerWidth > 900 || !sidebar.classList.contains("open")) return;
      if (!sidebar.contains(event.target) && !toggle.contains(event.target)) {
        closeSidebar();
      }
    });

    sidebar.addEventListener("click", (event) => {
      if (window.innerWidth <= 900 && event.target.closest("a")) {
        closeSidebar();
      }
    });
  }

  const appShell = document.querySelector(".app-shell");
  const collapseToggle = document.querySelector(".sidebar-collapse-toggle");
  if (appShell && sidebar && collapseToggle) {
    const storageKey = "akaryakit.sidebar.collapsed";
    const navLinks = [...sidebar.querySelectorAll(".nav a")];
    const isDesktop = () => window.innerWidth > 900;
    const getLinkLabel = (link) => {
      const label = [...link.children].find((child) => !child.classList.contains("nav-icon"));
      return label ? label.textContent.trim() : "";
    };
    const syncLinkLabels = (collapsed) => {
      navLinks.forEach((link) => {
        const label = getLinkLabel(link);
        if (!label) return;
        if (collapsed) {
          link.setAttribute("title", label);
          link.setAttribute("aria-label", label);
        } else {
          link.removeAttribute("title");
          link.removeAttribute("aria-label");
        }
      });
    };
    const applyCollapsed = (collapsed) => {
      const shouldCollapse = isDesktop() && collapsed;
      appShell.classList.toggle("sidebar-collapsed", shouldCollapse);
      collapseToggle.setAttribute("aria-expanded", String(!shouldCollapse));
      collapseToggle.setAttribute("aria-label", shouldCollapse ? "Menüyü genişlet" : "Menüyü daralt");
      collapseToggle.setAttribute("title", shouldCollapse ? "Menüyü genişlet" : "Menüyü daralt");
      syncLinkLabels(shouldCollapse);
    };

    let collapsedPreference = false;
    try {
      collapsedPreference = localStorage.getItem(storageKey) === "true";
    } catch (_error) {
      collapsedPreference = false;
    }
    applyCollapsed(collapsedPreference);

    collapseToggle.addEventListener("click", () => {
      collapsedPreference = !appShell.classList.contains("sidebar-collapsed");
      applyCollapsed(collapsedPreference);
      try {
        localStorage.setItem(storageKey, String(collapsedPreference));
      } catch (_error) {
        // Sidebar still works when browser storage is unavailable.
      }
    });
    window.addEventListener("resize", () => applyCollapsed(collapsedPreference));
  }

  const navigation = document.querySelector(".nav");
  if (navigation) {
    const storageKey = "akaryakit.sidebar.scrollTop";
    let savedScroll = null;
    try {
      const storedValue = sessionStorage.getItem(storageKey);
      if (storedValue !== null) savedScroll = Number(storedValue);
    } catch (_error) {
      savedScroll = null;
    }

    if (Number.isFinite(savedScroll)) {
      navigation.scrollTop = savedScroll;
    } else {
      const activeLink = navigation.querySelector("a.active");
      if (activeLink) activeLink.scrollIntoView({ block: "nearest" });
    }

    let scrollFrame = null;
    const persistScroll = () => {
      try {
        sessionStorage.setItem(storageKey, String(navigation.scrollTop));
      } catch (_error) {
        // The sidebar still works when browser storage is unavailable.
      }
    };
    const rememberScroll = () => {
      if (scrollFrame !== null) return;
      scrollFrame = requestAnimationFrame(() => {
        persistScroll();
        scrollFrame = null;
      });
    };
    navigation.addEventListener("scroll", rememberScroll, { passive: true });
    navigation.addEventListener("click", (event) => {
      if (event.target.closest("a")) persistScroll();
    });
  }

  const rowActionMenus = [...document.querySelectorAll(".row-action-menu")];
  if (rowActionMenus.length) {
    rowActionMenus.forEach((menu) => {
      menu.addEventListener("toggle", () => {
        const visualCard = menu.closest(".storage-visual-card");
        if (visualCard) visualCard.classList.toggle("row-menu-open", menu.open);
        if (!menu.open) return;
        rowActionMenus.forEach((otherMenu) => {
          if (otherMenu !== menu) otherMenu.open = false;
        });
      });
    });
    document.addEventListener("click", (event) => {
      rowActionMenus.forEach((menu) => {
        if (!menu.contains(event.target)) menu.open = false;
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        rowActionMenus.forEach((menu) => {
          menu.open = false;
        });
      }
    });
  }

  const storageViewToggle = document.querySelector("[data-storage-view-toggle]");
  if (storageViewToggle) {
    const storageViewKey = "akaryakit.storage.view";
    const viewButtons = [...storageViewToggle.querySelectorAll("[data-storage-view]")];
    const viewPanels = [...document.querySelectorAll("[data-storage-view-panel]")];
    const applyStorageView = (view) => {
      viewButtons.forEach((button) => {
        const isActive = button.dataset.storageView === view;
        button.classList.toggle("active", isActive);
        button.setAttribute("aria-pressed", String(isActive));
      });
      viewPanels.forEach((panel) => {
        panel.hidden = panel.dataset.storageViewPanel !== view;
      });
    };

    let savedStorageView = "list";
    try {
      savedStorageView = localStorage.getItem(storageViewKey) === "tank" ? "tank" : "list";
    } catch (_error) {
      savedStorageView = "list";
    }
    applyStorageView(savedStorageView);

    viewButtons.forEach((button) => {
      button.addEventListener("click", () => {
        const nextView = button.dataset.storageView;
        applyStorageView(nextView);
        try {
          localStorage.setItem(storageViewKey, nextView);
        } catch (_error) {
          // The view still changes when browser storage is unavailable.
        }
      });
    });
  }

  const storageSloshSources = [...document.querySelectorAll("[data-storage-slosh-source]")];
  if (storageSloshSources.length) {
    let sloshState = null;
    let settleTimer = null;
    let settlingCard = null;
    const stopSettling = () => {
      if (settleTimer !== null) window.clearTimeout(settleTimer);
      settleTimer = null;
      if (settlingCard) settlingCard.classList.remove("storage-sloshing", "storage-sloshing-settle");
      settlingCard = null;
    };
    const clearSlosh = (state) => {
      state.card.classList.remove("storage-sloshing", "storage-sloshing-settle");
      if (sloshState === state) sloshState = null;
    };

    storageSloshSources.forEach((source) => {
      source.addEventListener("pointerdown", (event) => {
        if (event.button !== 0) return;
        stopSettling();
        if (sloshState) clearSlosh(sloshState);
        const card = source.closest(".storage-visual-card");
        if (!card) return;
        sloshState = {
          source,
          card,
          pointerId: event.pointerId,
          startX: event.clientX,
          startY: event.clientY,
          moved: false,
        };
        source.setPointerCapture?.(event.pointerId);
        event.preventDefault();
      });

      source.addEventListener("pointermove", (event) => {
        const state = sloshState;
        if (!state || state.source !== source || state.pointerId !== event.pointerId) return;
        if (!state.moved && Math.hypot(event.clientX - state.startX, event.clientY - state.startY) < 6) return;
        state.moved = true;
        state.card.classList.add("storage-sloshing");
      });

      source.addEventListener("pointerup", (event) => {
        const state = sloshState;
        if (!state || state.source !== source || state.pointerId !== event.pointerId) return;
        source.releasePointerCapture?.(event.pointerId);
        if (!state.moved) {
          clearSlosh(state);
          return;
        }
        sloshState = null;
        settlingCard = state.card;
        state.card.classList.add("storage-sloshing-settle");
        settleTimer = window.setTimeout(() => {
          if (settlingCard !== state.card) return;
          state.card.classList.remove("storage-sloshing", "storage-sloshing-settle");
          settlingCard = null;
          settleTimer = null;
        }, 1050);
      });

      source.addEventListener("pointercancel", () => {
        if (sloshState?.source === source) clearSlosh(sloshState);
      });
    });
  }

  document.querySelectorAll("select[data-searchable]").forEach((select, index) => {
    const options = Array.from(select.options).map((option) => ({
      value: option.value,
      text: option.textContent,
      search: option.dataset.search || option.textContent,
      disabled: option.disabled,
    }));
    const controls = select.parentElement;
    const wrapper = document.createElement("div");
    const selectHost = document.createElement("div");
    const trigger = document.createElement("button");
    const panel = document.createElement("div");
    const search = document.createElement("input");
    const list = document.createElement("div");
    const listId = `searchable-options-${index}`;

    wrapper.className = "searchable-select";
    trigger.type = "button";
    trigger.className = "searchable-select-trigger";
    trigger.setAttribute("aria-haspopup", "listbox");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", listId);
    panel.className = "searchable-select-panel";
    panel.hidden = true;
    search.type = "search";
    search.className = "searchable-select-search";
    search.placeholder = select.dataset.searchPlaceholder || "Listede ara";
    search.autocomplete = "off";
    search.setAttribute("aria-label", search.placeholder);
    list.id = listId;
    list.className = "searchable-select-options";
    list.setAttribute("role", "listbox");

    select.classList.add("searchable-select-native");
    selectHost.className = "searchable-select-host";
    select.replaceWith(selectHost);
    selectHost.append(wrapper);
    wrapper.append(select, trigger, panel);
    panel.append(search, list);

    const normalize = (value) => value.toLocaleLowerCase("tr-TR").trim();
    const selectedOption = () => options.find((item) => item.value === select.value) || options[0];
    const close = () => {
      panel.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    };
    const open = () => {
      panel.hidden = false;
      trigger.setAttribute("aria-expanded", "true");
      search.focus();
    };
    const updateTrigger = () => {
      const text = selectedOption()?.text || "Seçiniz";
      trigger.textContent = text;
      trigger.title = text;
    };
    const render = () => {
      const query = normalize(search.value);
      list.replaceChildren();
      options.forEach((item) => {
        if (query && item.value && !normalize(item.search).includes(query)) return;
        const option = document.createElement("button");
        option.type = "button";
        option.className = "searchable-select-option";
        option.textContent = item.text;
        option.title = item.text;
        option.disabled = item.disabled;
        option.setAttribute("role", "option");
        option.setAttribute("aria-selected", String(item.value === select.value));
        option.addEventListener("click", () => {
          select.value = item.value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
          updateTrigger();
          search.value = "";
          close();
        });
        list.appendChild(option);
      });
    };

    trigger.addEventListener("click", () => {
      if (panel.hidden) {
        render();
        open();
      } else {
        close();
      }
    });
    search.addEventListener("input", render);
    search.addEventListener("keydown", (event) => {
      if (event.key === "Escape") close();
    });
    select.addEventListener("change", updateTrigger);
    document.addEventListener("click", (event) => {
      if (!wrapper.contains(event.target)) close();
    });

    updateTrigger();
    render();
  });

  const formatOperationNumber = (value) => new Intl.NumberFormat("tr-TR", {
    maximumFractionDigits: 3,
  }).format(Number(value));

  const operationStorageStatusNode = document.querySelector("#operation-storage-status");
  if (operationStorageStatusNode) {
    let storageStatus = {};
    try {
      storageStatus = JSON.parse(operationStorageStatusNode.textContent);
    } catch (_error) {
      storageStatus = {};
    }

    document.querySelectorAll("select[data-storage-status]").forEach((select) => {
      const helper = document.createElement("small");
      helper.className = "help storage-live-help";
      helper.setAttribute("aria-live", "polite");
      select.closest(".field")?.append(helper);
      const litersInput = document.querySelector("#id_liters");

      const updateStorageStatus = () => {
        const status = storageStatus[select.value];
        if (!status) {
          helper.textContent = "";
          helper.classList.remove("negative");
          return;
        }

        const currentStockValue = Number(status.current_stock);
        const currentStock = formatOperationNumber(currentStockValue);
        if (select.dataset.storageStatus === "source") {
          const liters = Number(litersInput?.value.replace(",", "."));
          const projectedStock = currentStockValue - liters;
          if (currentStockValue < 0) {
            helper.textContent = `UYARI: Mevcut stok zaten eksi (${currentStock} L).`;
          } else if (Number.isFinite(liters) && projectedStock < 0) {
            helper.textContent = `UYARI: Bu işlem sonrası stok ${formatOperationNumber(projectedStock)} L olacak.`;
          } else {
            helper.textContent = `Mevcut stok: ${currentStock} L`;
          }
          helper.classList.toggle(
            "negative",
            currentStockValue < 0 || (Number.isFinite(liters) && projectedStock < 0),
          );
        } else if (status.capacity === null) {
          helper.textContent = `Mevcut stok: ${currentStock} L · Kapasite tanımlanmamış`;
          helper.classList.remove("negative");
        } else {
          const capacity = formatOperationNumber(status.capacity);
          const remaining = formatOperationNumber(status.remaining);
          helper.textContent = `Mevcut stok: ${currentStock} L · Kapasite: ${capacity} L · Boş alan: ${remaining} L`;
          helper.classList.remove("negative");
        }
      };

      select.addEventListener("change", updateStorageStatus);
      litersInput?.addEventListener("input", updateStorageStatus);
      updateStorageStatus();
    });

    const adjustmentStorage = document.querySelector(
      'select[data-storage-status="adjustment"]',
    );
    const countedStockInput = document.querySelector("#id_counted_stock");
    const adjustmentField = countedStockInput?.closest(".field");
    if (adjustmentStorage && countedStockInput && adjustmentField) {
      const preview = document.createElement("small");
      preview.className = "help stock-adjustment-preview";
      preview.setAttribute("aria-live", "polite");
      adjustmentField.append(preview);

      const updateAdjustmentPreview = () => {
        const status = storageStatus[adjustmentStorage.value];
        const countedStock = Number(countedStockInput.value.replace(",", "."));
        if (!status || !Number.isFinite(countedStock) || countedStock < 0) {
          preview.textContent = "";
          preview.classList.remove("error");
          return;
        }

        const currentStock = Number(status.current_stock);
        const difference = countedStock - currentStock;
        const capacity = status.capacity === null ? null : Number(status.capacity);
        const aboveCapacity = capacity !== null && countedStock > capacity;

        if (difference === 0) {
          preview.textContent = "Fark yok · Düzeltme kaydı oluşturulmayacak";
        } else if (difference > 0) {
          preview.textContent = `Sayım farkı: +${formatOperationNumber(difference)} L · Stok artırılacak`;
        } else {
          preview.textContent = `Sayım farkı: ${formatOperationNumber(difference)} L · Stok azaltılacak`;
        }
        if (aboveCapacity) {
          preview.textContent += ` · Kapasite aşılıyor (${formatOperationNumber(capacity)} L)`;
        }
        preview.classList.toggle("error", aboveCapacity);
      };

      adjustmentStorage.addEventListener("change", updateAdjustmentPreview);
      countedStockInput.addEventListener("input", updateAdjustmentPreview);
      updateAdjustmentPreview();
    }
  }

  const purchaseUnitPriceInput = document.querySelector("#id_purchase_unit_price");
  if (purchaseUnitPriceInput) {
    const litersInput = document.querySelector("#id_liters");
    const priceField = purchaseUnitPriceInput.closest(".field");
    const helper = priceField?.querySelector(".help");
    const updatePurchaseTotal = () => {
      const liters = Number(litersInput?.value.replace(",", "."));
      const unitPrice = Number(purchaseUnitPriceInput.value.replace(",", "."));
      if (!Number.isFinite(liters) || !Number.isFinite(unitPrice)) {
        if (helper) {
          helper.textContent = "İrsaliyedeki veya faturadaki gerçek alış fiyatını girin.";
        }
        return;
      }
      const total = new Intl.NumberFormat("tr-TR", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(liters * unitPrice);
      if (helper) helper.textContent = `Hesaplanan alış tutarı: ${total} TL`;
    };
    litersInput?.addEventListener("input", updatePurchaseTotal);
    purchaseUnitPriceInput.addEventListener("input", updatePurchaseTotal);
    updatePurchaseTotal();
  }

  const vehicleMeterStatusNode = document.querySelector("#vehicle-meter-status");
  const vehicleSelect = document.querySelector("select[data-vehicle-meter]");
  const meterInput = document.querySelector("#id_meter_value");
  const meterField = meterInput?.closest(".field");
  const meterLabel = meterField?.querySelector(`label[for="${meterInput.id}"]`);
  if (vehicleMeterStatusNode && vehicleSelect && meterInput && meterField && meterLabel) {
    let vehicleMeterStatus = {};
    try {
      vehicleMeterStatus = JSON.parse(vehicleMeterStatusNode.textContent);
    } catch (_error) {
      vehicleMeterStatus = {};
    }

    const helper = document.createElement("small");
    helper.className = "help vehicle-meter-help";
    helper.setAttribute("aria-live", "polite");
    meterField.append(helper);

    const setMeterLabel = (text, required) => {
      meterLabel.replaceChildren(document.createTextNode(text));
      if (required) {
        const marker = document.createElement("span");
        marker.className = "required";
        marker.textContent = "*";
        meterLabel.append(marker);
      }
    };

    const updateMeterField = () => {
      const status = vehicleMeterStatus[vehicleSelect.value];
      if (!status) {
        meterInput.disabled = true;
        meterInput.removeAttribute("aria-required");
        meterInput.placeholder = "Önce araç veya makine seçin";
        setMeterLabel("Kilometre / çalışma saati", false);
        helper.textContent = "";
        return;
      }

      if (status.is_contractor) {
        meterInput.value = "";
        meterInput.disabled = true;
        meterInput.removeAttribute("aria-required");
        meterInput.placeholder = "Taşeron araçlarda sayaç girilmez";
        setMeterLabel("Sayaç", false);
        helper.textContent = "Taşeron dolumu: kilometre veya çalışma saati girilmez.";
        return;
      }

      if (status.meter_type === "NONE") {
        meterInput.value = "";
        meterInput.disabled = true;
        meterInput.removeAttribute("aria-required");
        meterInput.placeholder = "Bu araçta sayaç kullanılmıyor";
        setMeterLabel("Sayaç", false);
        helper.textContent = "Bu araç için sayaç takibi yapılmıyor.";
        return;
      }

      meterInput.disabled = false;
      meterInput.setAttribute("aria-required", "true");
      meterInput.placeholder = `${status.label} değerini girin`;
      setMeterLabel(status.label, true);
      helper.textContent = status.last_value === null
        ? "Bu araç için henüz sayaç kaydı yok."
        : `Son kayıt: ${formatOperationNumber(status.last_value)} ${status.unit}`;
    };

    vehicleSelect.addEventListener("change", () => {
      meterInput.value = "";
      updateMeterField();
    });
    updateMeterField();
  }

  const contractorPriceDefinitionsNode = document.querySelector(
    "#contractor-price-definitions",
  );
  const referencePriceInput = document.querySelector(
    "#id_contractor_reference_unit_price",
  );
  const contractorPriceInput = document.querySelector("#id_contractor_unit_price");
  if (
    vehicleMeterStatusNode
    && vehicleSelect
    && referencePriceInput
    && contractorPriceInput
  ) {
    let vehicleStatus = {};
    let priceDefinitions = [];
    try {
      vehicleStatus = JSON.parse(vehicleMeterStatusNode.textContent);
    } catch (_error) {
      vehicleStatus = {};
    }
    try {
      const parsed = contractorPriceDefinitionsNode
        ? JSON.parse(contractorPriceDefinitionsNode.textContent)
        : [];
      priceDefinitions = Array.isArray(parsed) ? parsed : [];
    } catch (_error) {
      priceDefinitions = [];
    }

    const referenceField = referencePriceInput.closest(".field");
    const contractorField = contractorPriceInput.closest(".field");
    const occurredAtInput = document.querySelector("#id_occurred_at");
    const litersInput = document.querySelector("#id_liters");
    const priceHelper = document.createElement("small");
    priceHelper.className = "help contractor-price-help";
    priceHelper.setAttribute("aria-live", "polite");
    contractorField?.append(priceHelper);

    const setConditionalRequired = (field, required) => {
      const label = field?.querySelector("label");
      const input = field?.querySelector("input");
      if (!label || !input) return;
      let marker = label.querySelector("[data-conditional-required]");
      if (required && !marker) {
        marker = document.createElement("span");
        marker.className = "required";
        marker.dataset.conditionalRequired = "true";
        marker.textContent = "*";
        label.append(marker);
      } else if (!required && marker) {
        marker.remove();
      }
      if (required) input.setAttribute("aria-required", "true");
      else input.removeAttribute("aria-required");
    };

    const definitionAt = (dateValue) => {
      const operationTime = new Date(dateValue || Date.now()).getTime();
      if (!Number.isFinite(operationTime)) return null;
      return priceDefinitions.reduce((selected, item) => {
        const effectiveTime = new Date(item.effective_from).getTime();
        if (!Number.isFinite(effectiveTime) || effectiveTime > operationTime) {
          return selected;
        }
        return !selected || effectiveTime >= selected.effectiveTime
          ? { ...item, effectiveTime }
          : selected;
      }, null);
    };

    const updatePricePreview = () => {
      const liters = Number(litersInput?.value.replace(",", "."));
      const reference = Number(referencePriceInput.value.replace(",", "."));
      const contractor = Number(contractorPriceInput.value.replace(",", "."));
      if (![liters, reference, contractor].every(Number.isFinite)) {
        priceHelper.textContent = "Fiyatlar TL/L olarak hareket kaydına sabitlenir.";
        return;
      }
      const referenceTotal = liters * reference;
      const chargedTotal = liters * contractor;
      const formatMoney = (value) => new Intl.NumberFormat("tr-TR", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      }).format(value);
      priceHelper.textContent = `Referans ${formatMoney(referenceTotal)} TL · Yansıtılacak ${formatMoney(chargedTotal)} TL · Fark ${formatMoney(chargedTotal - referenceTotal)} TL`;
    };

    const updateContractorPricing = ({ applyDefinition = false } = {}) => {
      const isContractor = Boolean(vehicleStatus[vehicleSelect.value]?.is_contractor);
      [referenceField, contractorField].forEach((field) => {
        if (field) field.hidden = !isContractor;
      });
      referencePriceInput.disabled = !isContractor;
      contractorPriceInput.disabled = !isContractor;
      setConditionalRequired(referenceField, isContractor);
      setConditionalRequired(contractorField, isContractor);
      if (!isContractor) {
        priceHelper.textContent = "";
        return;
      }

      if (
        applyDefinition
        || (!referencePriceInput.value && !contractorPriceInput.value)
      ) {
        const definition = definitionAt(occurredAtInput?.value);
        referencePriceInput.value = definition?.reference_unit_price || "";
        contractorPriceInput.value = definition?.contractor_unit_price || "";
        if (!definition) {
          priceHelper.textContent = "Bu işlem tarihi için fiyat tanımı bulunmuyor.";
          return;
        }
      }
      updatePricePreview();
    };

    vehicleSelect.addEventListener("change", () => {
      updateContractorPricing({ applyDefinition: true });
    });
    occurredAtInput?.addEventListener("change", () => {
      updateContractorPricing({ applyDefinition: true });
    });
    litersInput?.addEventListener("input", updatePricePreview);
    referencePriceInput.addEventListener("input", updatePricePreview);
    contractorPriceInput.addEventListener("input", updatePricePreview);
    updateContractorPricing();
  }

  const approvalSelectAll = document.querySelector("#approval-select-all");
  const approvalCheckboxes = [...document.querySelectorAll(".approval-row-checkbox")];
  const bulkApprovalButton = document.querySelector("#bulk-approve-button");
  const bulkApprovalLabel = document.querySelector("#bulk-approve-label");
  const bulkApprovalForm = document.querySelector("#bulk-approval-form");
  if (approvalSelectAll && bulkApprovalButton && bulkApprovalLabel && bulkApprovalForm) {
    const updateApprovalSelection = () => {
      const selectedCount = approvalCheckboxes.filter((checkbox) => checkbox.checked).length;
      approvalSelectAll.checked = approvalCheckboxes.length > 0
        && selectedCount === approvalCheckboxes.length;
      approvalSelectAll.indeterminate = selectedCount > 0
        && selectedCount < approvalCheckboxes.length;
      bulkApprovalButton.disabled = selectedCount === 0;
      bulkApprovalLabel.textContent = selectedCount
        ? `Seçilen ${selectedCount} kaydı onayla`
        : "Seçilenleri onayla";
    };

    approvalSelectAll.addEventListener("change", () => {
      approvalCheckboxes.forEach((checkbox) => {
        checkbox.checked = approvalSelectAll.checked;
      });
      updateApprovalSelection();
    });
    approvalCheckboxes.forEach((checkbox) => {
      checkbox.addEventListener("change", updateApprovalSelection);
    });
    bulkApprovalForm.addEventListener("submit", (event) => {
      const selectedCount = approvalCheckboxes.filter((checkbox) => checkbox.checked).length;
      if (!selectedCount) {
        event.preventDefault();
        return;
      }
      // JS varsa tek adımda onayla; JS yoksa sunucu özet sayfası gösterir.
      if (window.confirm(`${selectedCount} saha kaydı onaylansın mı?`)) {
        let confirmField = bulkApprovalForm.querySelector('input[name="confirm"]');
        if (!confirmField) {
          confirmField = document.createElement("input");
          confirmField.type = "hidden";
          confirmField.name = "confirm";
          bulkApprovalForm.append(confirmField);
        }
        confirmField.value = "1";
      } else {
        event.preventDefault();
      }
    });
    updateApprovalSelection();
  }

  // Çift gönderim koruması: POST formları art arda ikinci kez gönderilemez.
  // Diğer dinleyicilerden sonra çalışır; iptal edilen gönderimleri kilitlemez.
  document.querySelectorAll('form[method="post" i]').forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (form.dataset.submitting === "1") {
        event.preventDefault();
        return;
      }
      form.dataset.submitting = "1";
      const submitter = event.submitter;
      if (submitter) {
        window.setTimeout(() => {
          submitter.setAttribute("disabled", "");
        }, 0);
      }
    });
  });
})();
